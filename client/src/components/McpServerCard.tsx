import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Link2,
  Settings2,
  Star,
  Wrench,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import McpWorkspacePanel, {
  type McpWorkspace,
} from "./McpWorkspacePanel";
import McpCredentialPanel from "./McpCredentialPanel";
import McpApprovalDialog, {
  type McpCatalogApprovalRequest,
} from "./McpApprovalDialog";
import McpBrowserPanel from "./McpBrowserPanel";
import McpCatalogRemotePanel from "./McpCatalogRemotePanel";
import {
  mcpRequirementLabels,
  type McpProject,
} from "../data/mcpProjects";
import {
  formatMcpIsolation,
  mcpAvailabilityLabels,
  mcpConnectionKindLabels,
  type McpCatalogAdapterStatus,
} from "../data/mcpAdaptationPlan";

type ConnectionState = "idle" | "connecting" | "connected" | "error";
type InstallState = "idle" | "checking" | "installing" | "installed" | "error";

interface JsonSchemaProperty {
  type?: string | string[];
  title?: string;
  description?: string;
  enum?: Array<string | number | boolean>;
  default?: unknown;
  items?: JsonSchemaProperty;
  properties?: Record<string, JsonSchemaProperty>;
  "x-modelmirror-input"?:
    | "workspace-file"
    | "workspace-directory"
    | "artifact-name"
    | "browser-url"
    | "browser-ref";
}

interface ToolSchema {
  type?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
}

export interface McpTool {
  name: string;
  title?: string | null;
  description?: string | null;
  inputSchema: ToolSchema;
}

interface ToolCallResult {
  content: Array<Record<string, unknown>>;
  is_error: boolean;
  raw: Record<string, unknown>;
  artifacts?: Array<Record<string, unknown>>;
  idempotency_key?: string;
  idempotent_replay?: boolean;
  unknown_outcome?: boolean;
}

interface BrowserElementOption {
  ref: string;
  label: string;
  role: string;
}

interface CatalogOperationError {
  code: "approval_required" | "provider_rate_limited" | "unknown_outcome" | string;
  message: string;
  approval_id?: string;
  summary?: string;
  argument_digest?: string;
  expires_at?: number | string;
  idempotency_key?: string;
  retry_after_seconds?: number;
  target_preview?: McpCatalogApprovalRequest["target_preview"];
  context_kind?: McpCatalogApprovalRequest["context_kind"];
}

interface ToolOperationNotice {
  kind: "rate-limited" | "unknown-outcome" | "idempotent-replay" | "completed";
  message: string;
  idempotencyKey?: string;
  retryAfterSeconds?: number;
}

interface InstalledMcpRecord {
  project_id: string;
}

const databasePreflightCopy: Record<
  McpCatalogAdapterStatus["preflight_status"],
  { label: string; className: string }
> = {
  "not-applicable": {
    label: "不适用",
    className: "text-slate-300",
  },
  blocked: {
    label: "数据源验证已阻断",
    className: "text-rose-100",
  },
  "awaiting-workspace": {
    label: "等待封存并绑定数据库文件",
    className: "text-amber-100",
  },
  "awaiting-configuration": {
    label: "等待保存连接字段和加密凭据",
    className: "text-amber-100",
  },
  unverified: {
    label: "配置已保存，等待连接预检",
    className: "text-amber-100",
  },
  verifying: {
    label: "正在验证数据库目标与只读能力",
    className: "text-cyan-100",
  },
  verified: {
    label: "数据源预检与代表性只读调用通过",
    className: "text-emerald-100",
  },
  failed: {
    label: "数据源验证失败，请检查受控配置",
    className: "text-rose-100",
  },
};

const saasPreflightCopy: Record<
  McpCatalogAdapterStatus["preflight_status"],
  { label: string; className: string }
> = {
  "not-applicable": { label: "等待账号配置", className: "text-slate-300" },
  blocked: { label: "账号适配已阻断", className: "text-rose-100" },
  "awaiting-workspace": { label: "等待资源范围", className: "text-amber-100" },
  "awaiting-configuration": {
    label: "等待保存账号与资源范围",
    className: "text-amber-100",
  },
  unverified: { label: "配置已保存，等待连接预检", className: "text-amber-100" },
  verifying: { label: "正在验证账号、权限和资源范围", className: "text-cyan-100" },
  verified: {
    label: "账号、最小权限与资源范围预检通过",
    className: "text-emerald-100",
  },
  failed: { label: "账号预检失败，请检查受控配置", className: "text-rose-100" },
};

const saasAccountStatusCopy: Record<
  NonNullable<McpCatalogAdapterStatus["account_status"]>,
  { label: string; className: string }
> = {
  "not-applicable": { label: "不适用", className: "text-slate-300" },
  blocked: { label: "账号绑定已阻断", className: "text-rose-100" },
  unbound: { label: "尚未绑定账号", className: "text-slate-300" },
  unverified: { label: "账号已配置，尚未验证", className: "text-amber-100" },
  verified: { label: "账号绑定已验证", className: "text-emerald-100" },
};

interface McpServerCardProps {
  project: McpProject;
  adapterStatus?: McpCatalogAdapterStatus;
  restoredSession?: McpSessionSummary;
  onConnectionChange?: () => void;
}

export interface McpSessionSummary {
  session_id: string;
  server_command: string[];
  status: string;
  created_at: number;
  uptime_seconds: number;
  idle_seconds: number;
  tools_count: number;
}

function formatStars(stars: number) {
  if (stars <= 0) return "官方包";
  if (stars >= 1000) return `${(stars / 1000).toFixed(1)}k`;
  return stars.toLocaleString("zh-CN");
}

function compactUnavailableReason(
  availability: McpProject["availability"],
  limitations: string[],
  requirements: McpProject["requirements"],
) {
  const evidence = limitations.join(" ");
  if (/归档|archiv/i.test(evidence)) return "上游项目已归档，暂不接入。";
  if (/许可|license/i.test(evidence)) return "许可证信息尚未完成核验。";
  if (/任意.*代码|代码执行|shell/i.test(evidence)) {
    return "需要执行代码，当前安全沙箱尚未开放。";
  }
  if (/Docker Socket|docker.*权限/i.test(evidence)) {
    return "需要 Docker 管理权限，当前不会开放。";
  }
  if (/登录态|浏览器扩展|本机浏览器/i.test(evidence)) {
    return "依赖浏览器登录态，当前不会继承用户会话。";
  }
  if (/schema 漂移|上游.*契约|契约漂移/i.test(evidence)) {
    return "上游接口不稳定，恢复兼容后再开放。";
  }
  if (requirements.includes("desktop-host")) {
    return "需要桌面宿主，当前运行环境不支持。";
  }
  if (requirements.includes("system-permission")) {
    return "需要高权限运行，当前安全边界不允许。";
  }
  if (requirements.includes("oauth") || requirements.includes("account-binding")) {
    return "账号授权与权限边界尚未完成验证。";
  }
  if (requirements.includes("external-runtime")) {
    return "所需运行环境尚未纳入受控部署。";
  }
  if (availability === "adapting") return "正在完成连接、权限和回退验证。";
  if (availability === "blocked") return "当前接入方式尚未满足安全要求。";
  return "已进入适配计划，完成验证后开放。";
}

function schemaType(property: JsonSchemaProperty) {
  if (Array.isArray(property.type)) {
    return property.type.find((type) => type !== "null") ?? "string";
  }
  return property.type ?? "string";
}

function defaultFieldValue(property: JsonSchemaProperty) {
  if (property.default !== undefined) return String(property.default);
  const type = schemaType(property);
  if (type === "boolean") return "false";
  if (type === "number" || type === "integer") return "";
  if (type === "array") return "[]";
  if (type === "object") return "{}";
  return "";
}

function coerceFieldValue(property: JsonSchemaProperty, value: string) {
  const type = schemaType(property);
  if (property.enum?.length) return value;
  if (type === "boolean") return value === "true";
  if (type === "number" || type === "integer") {
    if (!value.trim()) return undefined;
    const parsed = Number(value);
    if (Number.isNaN(parsed)) throw new Error("数字参数格式不正确");
    return type === "integer" ? Math.trunc(parsed) : parsed;
  }
  if (type === "array" || type === "object") {
    if (!value.trim()) return type === "array" ? [] : {};
    return JSON.parse(value);
  }
  return value;
}

function contentToMarkdown(result: ToolCallResult | null) {
  if (!result) return "";
  return result.content
    .map((item) => {
      if (typeof item.text === "string") return item.text;
      if (typeof item.data === "string") return item.data;
      return `\`\`\`json\n${JSON.stringify(item, null, 2)}\n\`\`\``;
    })
    .join("\n\n");
}

function browserElementsFromResult(result: ToolCallResult): BrowserElementOption[] | null {
  const raw = result.raw;
  const structured =
    raw.structuredContent && typeof raw.structuredContent === "object"
      ? raw.structuredContent as Record<string, unknown>
      : raw.structured_content && typeof raw.structured_content === "object"
        ? raw.structured_content as Record<string, unknown>
        : null;
  const elements = structured?.elements;
  if (!Array.isArray(elements)) return null;
  const seen = new Set<string>();
  const options: BrowserElementOption[] = [];
  for (const value of elements) {
    if (!value || typeof value !== "object") continue;
    const item = value as Record<string, unknown>;
    const ref = typeof item.ref === "string" ? item.ref : "";
    if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(ref) || seen.has(ref)) continue;
    seen.add(ref);
    options.push({
      ref,
      label: typeof item.label === "string" && item.label.trim()
        ? item.label.trim().slice(0, 120)
        : "未命名元素",
      role: typeof item.role === "string" && item.role.trim()
        ? item.role.trim().slice(0, 48)
        : "元素",
    });
  }
  return options;
}

function isCatalogOperationError(value: unknown): value is CatalogOperationError {
  return Boolean(
    value &&
    typeof value === "object" &&
    typeof (value as CatalogOperationError).code === "string" &&
    typeof (value as CatalogOperationError).message === "string",
  );
}

function approvalFromError(
  detail: CatalogOperationError,
): McpCatalogApprovalRequest | null {
  if (
    detail.code !== "approval_required" ||
    !detail.approval_id ||
    !detail.argument_digest ||
    detail.expires_at === undefined
  ) return null;
  return {
    code: "approval_required",
    message: detail.message,
    approval_id: detail.approval_id,
    summary: detail.summary,
    argument_digest: detail.argument_digest,
    expires_at: detail.expires_at,
    idempotency_key: detail.idempotency_key,
    target_preview: detail.target_preview,
    context_kind: detail.context_kind,
  };
}

function noticeFromError(detail: CatalogOperationError): ToolOperationNotice | null {
  if (detail.code === "provider_rate_limited") {
    return {
      kind: "rate-limited",
      message: detail.message || "上游服务已限流。操作不会自动重试，请稍后重新预览。",
      retryAfterSeconds: detail.retry_after_seconds,
    };
  }
  if (detail.code === "unknown_outcome") {
    return {
      kind: "unknown-outcome",
      message: detail.message || "操作结果未知，禁止自动重试。请先核对目标状态。",
      idempotencyKey: detail.idempotency_key,
    };
  }
  return null;
}

function noticeFromResult(result: ToolCallResult): ToolOperationNotice | null {
  if (result.unknown_outcome) {
    return {
      kind: "unknown-outcome",
      message: "操作结果未知，禁止自动重试。请先核对目标状态。",
      idempotencyKey: result.idempotency_key,
    };
  }
  if (result.idempotent_replay) {
    return {
      kind: "idempotent-replay",
      message: "检测到重复确认，本次复用了已有结果，没有再次执行。",
      idempotencyKey: result.idempotency_key,
    };
  }
  if (result.idempotency_key) {
    return {
      kind: "completed",
      message: "本次操作已按幂等键完成；重复确认不会再次执行。",
      idempotencyKey: result.idempotency_key,
    };
  }
  return null;
}

function shortIdempotencyKey(value?: string) {
  if (!value) return "";
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value;
}

const browserSensitiveQueryKeys = new Set([
  "accesstoken",
  "apikey",
  "authorization",
  "auth",
  "bearer",
  "code",
  "credential",
  "idtoken",
  "jwt",
  "key",
  "keypairid",
  "oauth",
  "oauthtoken",
  "password",
  "passwd",
  "refreshtoken",
  "secret",
  "session",
  "sessionid",
  "sig",
  "signature",
  "token",
]);

function browserQueryHasSensitiveKey(query: string) {
  let decoded = query;
  for (let index = 0; index < 2; index += 1) {
    try {
      decoded = decodeURIComponent(decoded);
    } catch {
      return true;
    }
  }
  return decoded.split(/[&;?#]+/).some((component) => {
    const rawKey = component.split("=", 1)[0] ?? "";
    const key = rawKey.toLowerCase().replace(/[^a-z0-9]+/g, "");
    return Boolean(
      key &&
        (browserSensitiveQueryKeys.has(key) ||
          key.startsWith("oauth") ||
          key.startsWith("xamz") ||
          key.startsWith("xgoog") ||
          key.endsWith("secret") ||
          key.endsWith("signature") ||
          key.endsWith("token")),
    );
  });
}

function validateBrowserUrl(value: string) {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("浏览器目标必须是完整的公网 HTTP/HTTPS 地址");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("浏览器目标只允许 HTTP 或 HTTPS");
  }
  if (parsed.username || parsed.password) {
    throw new Error("浏览器目标不能包含用户名或密码");
  }
  if (parsed.port && parsed.port !== "80" && parsed.port !== "443") {
    throw new Error("浏览器目标只允许 80 或 443 端口");
  }
  if (browserQueryHasSensitiveKey(parsed.search.slice(1))) {
    throw new Error("浏览器导航 URL 不接受 Token、签名或其他敏感查询参数");
  }
  const hostname = parsed.hostname.toLowerCase();
  if (
    !hostname.includes(".") ||
    hostname === "localhost" ||
    /^\d{1,3}(?:\.\d{1,3}){3}$/.test(hostname) ||
    hostname.includes(":")
  ) {
    throw new Error("浏览器目标必须是完整公网域名；本机名、内部服务名和 IP 地址不可用");
  }
  return value;
}

export default function McpServerCard({
  project,
  adapterStatus,
  restoredSession,
  onConnectionChange,
}: McpServerCardProps) {
  const [state, setState] = useState<ConnectionState>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [tools, setTools] = useState<McpTool[]>([]);
  const [error, setError] = useState("");
  const [formValues, setFormValues] = useState<Record<string, Record<string, string>>>(
    {},
  );
  const [toolResults, setToolResults] = useState<Record<string, ToolCallResult>>({});
  const [toolNotices, setToolNotices] = useState<Record<string, ToolOperationNotice>>({});
  const [runningTool, setRunningTool] = useState<string | null>(null);
  const [boundWorkspace, setBoundWorkspace] = useState<McpWorkspace | null>(null);
  const [catalogConfigured, setCatalogConfigured] = useState(
    adapterStatus?.configured ?? false,
  );
  const [catalogSettings, setCatalogSettings] = useState<
    Record<string, string | number | boolean>
  >(adapterStatus?.configuration_values ?? {});
  const [catalogBindings, setCatalogBindings] = useState<Record<string, string>>(
    adapterStatus?.credential_bindings ?? {},
  );
  const [pendingApproval, setPendingApproval] = useState<McpCatalogApprovalRequest | null>(null);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const approvalCallbackRef = useRef<(() => void) | null>(null);
  const approvalToolRef = useRef<string | null>(null);
  const [isInstallOpen, setIsInstallOpen] = useState(false);
  const [isRemoteReviewOpen, setIsRemoteReviewOpen] = useState(false);
  const [installState, setInstallState] = useState<InstallState>("checking");
  const [installError, setInstallError] = useState("");
  const [browserRefreshKey, setBrowserRefreshKey] = useState(0);
  const [isWorkbenchOpen, setIsWorkbenchOpen] = useState(false);
  const [browserRefOptions, setBrowserRefOptions] = useState<BrowserElementOption[]>([]);
  const ignoredRestoredSessionRef = useRef<string | null>(null);
  const workbenchTriggerRef = useRef<HTMLButtonElement | null>(null);
  const workbenchDialogRef = useRef<HTMLDivElement | null>(null);
  const workbenchCloseRef = useRef<HTMLButtonElement | null>(null);

  const availability = adapterStatus?.availability ?? project.availability;
  const connectionKind = adapterStatus?.connection_kind ?? project.connectionKind;
  const wave = adapterStatus?.wave ?? project.adaptationWave;
  const limitations = adapterStatus?.limitations ?? project.adaptationLimitations;
  const isStatefulSaas = wave === 6;
  const isBrowserAdapter = wave === 7;
  const isPlaywrightCard = project.id === "playwright-mcp";
  const isReadyProductCard = availability === "ready";
  const isConnected = state === "connected" || adapterStatus?.connected === true;
  const remoteReviewEntryEnabled = Boolean(
    adapterStatus?.remote_review_capable && adapterStatus.remote_review_enabled,
  );
  const unavailableStatusLabel = remoteReviewEntryEnabled
    ? "待复核"
    : availability === "adapting"
      ? "适配中"
      : "未适配";
  const unavailableReason = compactUnavailableReason(
    availability,
    limitations,
    project.requirements,
  );
  const statefulSaasGateEnabled =
    !isStatefulSaas || adapterStatus?.stateful_saas_gate_enabled === true;
  const canConnect =
    adapterStatus?.feature_enabled === true &&
    adapterStatus.executable === true &&
    availability === "ready" &&
    statefulSaasGateEnabled;
  const workspacePolicy = adapterStatus?.workspace_policy ?? null;
  const databasePolicy = adapterStatus?.database_policy ?? null;
  const browserPolicy = adapterStatus?.browser_policy ?? null;
  const databasePreflight = adapterStatus?.preflight_status ?? "not-applicable";
  const needsCatalogConfiguration = Boolean(
    adapterStatus?.credential_fields.length || adapterStatus?.setting_fields.length,
  );
  const canConfigureCatalog = Boolean(
    canConnect || adapterStatus?.remote_review_credential_ready,
  );
  const readyCardCopy = useMemo(() => {
    switch (project.id) {
      case "playwright-mcp":
        return {
          description: "打开网页、读取页面并执行受控点击、填写与截图。",
          tags: ["打开网页", "读取页面", "点击与填写", "生成截图"],
          connectionNote: "临时匿名浏览器，不保留登录态；不支持上传和下载。",
        };
      case "takashiishida-arxiv-latex-mcp":
        return {
          description: "读取 arXiv 论文摘要、章节结构和 LaTeX 正文。",
          tags: ["论文摘要", "章节目录", "LaTeX 正文", "只读访问"],
          connectionNote: "仅访问 arXiv 官方公开论文，不编译 TeX 或加载外部资源。",
        };
      case "greptimeteam-greptimedb-mcp-server":
        return {
          description: "查询固定 GreptimeDB 数据表的结构、时间范围和健康状态。",
          tags: ["表结构", "时序查询", "健康检查", "只读访问"],
          connectionNote: "连接固定数据源，只允许服务端生成的只读查询。",
        };
      case "victoriametrics-community-mcp-victoriametrics":
        return {
          description: "读取 VictoriaMetrics 指标、标签和限定时间范围的监控数据。",
          tags: ["指标列表", "标签查询", "即时查询", "范围查询"],
          connectionNote: "连接固定监控目标，查询范围和返回数量均受限制。",
        };
      default:
        return {
          description: project.description,
          tags: project.tags.slice(0, 4),
          connectionNote: workspacePolicy?.required
            ? "连接前需选择受控工作区"
            : needsCatalogConfiguration
              ? "连接前需完成一次配置"
              : "可直接连接并使用工具",
        };
    }
  }, [needsCatalogConfiguration, project.description, project.id, project.tags, workspacePolicy?.required]);
  const canStartConnection =
    canConnect &&
    (!isBrowserAdapter || Boolean(browserPolicy)) &&
    (!workspacePolicy?.required || Boolean(boundWorkspace)) &&
    (!needsCatalogConfiguration || catalogConfigured);
  const canInstall = canConnect && project.installMode === "one-click";
  const commandPreview = useMemo(
    () =>
      canConnect
        ? `${mcpConnectionKindLabels[connectionKind]} · 执行配置由服务端受控适配器管理`
        : `第 ${wave} 批 · ${mcpConnectionKindLabels[connectionKind]} · ${limitations[0] ?? "等待生产级验收"}`,
    [canConnect, connectionKind, limitations, wave],
  );
  useEffect(() => {
    if (!isWorkbenchOpen) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    workbenchCloseRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsWorkbenchOpen(false);
        workbenchTriggerRef.current?.focus();
      }
      if (event.key === "Tab") {
        const focusable = Array.from(
          workbenchDialogRef.current?.querySelectorAll<HTMLElement>(
            'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ) ?? [],
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isWorkbenchOpen]);

  function closeWorkbench() {
    setIsWorkbenchOpen(false);
    workbenchTriggerRef.current?.focus();
  }

  useEffect(() => {
    setCatalogConfigured(adapterStatus?.configured ?? false);
    setCatalogSettings(adapterStatus?.configuration_values ?? {});
    setCatalogBindings(adapterStatus?.credential_bindings ?? {});
  }, [
    adapterStatus?.configured,
    adapterStatus?.configuration_values,
    adapterStatus?.credential_bindings,
  ]);

  useEffect(() => {
    let cancelled = false;

    if (!canInstall) {
      setInstallState("idle");
      setInstallError("");
      return () => {
        cancelled = true;
      };
    }

    async function loadInstalledState() {
      setInstallState("checking");
      try {
        const response = await fetch("/api/mcp/installed");
        if (!response.ok) throw new Error(await readError(response));
        const data = (await response.json()) as { installed: InstalledMcpRecord[] };
        const installed = data.installed.some(
          (record) => record.project_id === project.id,
        );
        if (!cancelled) {
          setInstallState(installed ? "installed" : "idle");
          setInstallError("");
        }
      } catch (exc) {
        if (!cancelled) {
          setInstallState("idle");
          setInstallError(exc instanceof Error ? exc.message : "");
        }
      }
    }

    void loadInstalledState();
    return () => {
      cancelled = true;
    };
  }, [canInstall, project.id]);

  useEffect(() => {
    if (!adapterStatus?.connected) {
      ignoredRestoredSessionRef.current = null;
      return;
    }
    if (
      !restoredSession ||
      restoredSession.session_id === sessionId ||
      restoredSession.session_id === ignoredRestoredSessionRef.current
    ) return;
    setSessionId(restoredSession.session_id);
    setState("connected");
    setError("");
    void fetchTools().catch((exc) => {
      if (ignoredRestoredSessionRef.current === restoredSession.session_id) return;
      setState("error");
      setError(exc instanceof Error ? exc.message : "无法恢复 MCP 会话");
    });
  }, [adapterStatus?.connected, restoredSession, sessionId]);

  async function readErrorDetail(response: Response) {
    try {
      const data = (await response.json()) as {
        detail?: string | CatalogOperationError;
        error?: string;
      };
      return data.detail ?? data.error ?? response.statusText;
    } catch {
      return response.statusText;
    }
  }

  async function readError(response: Response) {
    const detail = await readErrorDetail(response);
    return typeof detail === "string" ? detail : detail.message;
  }

  async function connect() {
    if (!canStartConnection) return;
    ignoredRestoredSessionRef.current = null;
    setState("connecting");
    setError("");
    setTools([]);
    setToolResults({});
    setToolNotices({});
    setBrowserRefOptions([]);
    try {
      const response = await fetch(`/api/mcp/catalog/${project.id}/connect`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await readError(response));
      const data = (await response.json()) as { session_id: string };
      setSessionId(data.session_id);

      await fetchTools();
      setState("connected");
      setBrowserRefreshKey((current) => current + 1);
      if (isPlaywrightCard) setIsWorkbenchOpen(true);
      onConnectionChange?.();
    } catch (exc) {
      setState("error");
      setError(exc instanceof Error ? exc.message : "无法连接 MCP Server");
    }
  }

  async function fetchTools() {
    const toolsResponse = await fetch(`/api/mcp/catalog/${project.id}/tools`);
    if (!toolsResponse.ok) throw new Error(await readError(toolsResponse));
    const toolsData = (await toolsResponse.json()) as { tools: McpTool[] };
    setTools(toolsData.tools);
  }

  async function disconnect() {
    setError("");
    try {
      const response = await fetch(`/api/mcp/catalog/${project.id}/session`, {
        method: "DELETE",
      });
      if (!response.ok && response.status !== 404) {
        throw new Error(await readError(response));
      }
    } catch (exc) {
      setState("connected");
      setError(
        exc instanceof Error
          ? `结束会话失败：${exc.message}。临时浏览器可能仍在运行，请再次结束。`
          : "结束会话失败，临时浏览器可能仍在运行，请再次结束。",
      );
      return;
    }
    ignoredRestoredSessionRef.current =
      sessionId ?? restoredSession?.session_id ?? null;
    setSessionId(null);
    setTools([]);
    setToolResults({});
    setToolNotices({});
    setFormValues({});
    setBrowserRefOptions([]);
    setError("");
    setState("idle");
    setBrowserRefreshKey((current) => current + 1);
    onConnectionChange?.();
  }

  async function installProject() {
    if (!canInstall || installState === "installing" || installState === "installed") {
      return;
    }
    setInstallState("installing");
    setInstallError("");
    try {
      const response = await fetch(`/api/mcp/catalog/${project.id}/prepare`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await readError(response));
      setInstallState("installed");
    } catch (exc) {
      setInstallState("error");
      setInstallError(exc instanceof Error ? exc.message : "MCP 安装失败");
    }
  }

  function updateField(toolName: string, key: string, value: string) {
    setFormValues((current) => ({
      ...current,
      [toolName]: {
        ...current[toolName],
        [key]: value,
      },
    }));
  }

  function buildArguments(tool: McpTool) {
    const properties = tool.inputSchema.properties ?? {};
    const values = formValues[tool.name] ?? {};
    const args: Record<string, unknown> = {};
    for (const [key, property] of Object.entries(properties)) {
      const rawValue =
        values[key] ??
        (property["x-modelmirror-input"] === "workspace-file"
          ? boundWorkspace?.files[0]?.file_id ?? ""
          : defaultFieldValue(property));
      const coerced = coerceFieldValue(property, rawValue);
      if (property["x-modelmirror-input"] === "browser-url" && typeof coerced === "string") {
        args[key] = validateBrowserUrl(coerced);
      } else if (property["x-modelmirror-input"] === "browser-ref" && typeof coerced === "string") {
        if (
          !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(coerced) ||
          !browserRefOptions.some((option) => option.ref === coerced)
        ) {
          throw new Error("页面元素引用已失效；请重新执行页面快照并从受控列表选择");
        }
        args[key] = coerced;
      } else if (coerced !== undefined) {
        args[key] = coerced;
      }
    }
    return args;
  }

  async function callTool(tool: McpTool) {
    if (!sessionId) return;
    setRunningTool(tool.name);
    setError("");
    setToolResults((current) => {
      const next = { ...current };
      delete next[tool.name];
      return next;
    });
    setToolNotices((current) => {
      const next = { ...current };
      delete next[tool.name];
      return next;
    });
    try {
      const response = await fetch(
        `/api/mcp/catalog/${project.id}/tools/${encodeURIComponent(tool.name)}/call`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            arguments: buildArguments(tool),
          }),
        },
      );
      if (!response.ok) {
        const detail = await readErrorDetail(response);
        if (typeof detail !== "string" && isCatalogOperationError(detail)) {
          const approval = approvalFromError(detail);
          if (approval) {
            approvalToolRef.current = tool.name;
            showApproval(approval, () => {
              setToolResults((current) => ({ ...current }));
              if (boundWorkspace) void refreshBoundWorkspace(boundWorkspace.workspace_id);
              if (isBrowserAdapter) setBrowserRefreshKey((current) => current + 1);
            });
            return;
          }
          const notice = noticeFromError(detail);
          if (notice) {
            setToolNotices((current) => ({ ...current, [tool.name]: notice }));
            if (isBrowserAdapter && notice.kind === "unknown-outcome") {
              markBrowserSessionTainted(
                "浏览器操作结果未知，临时会话已销毁。请先核对目标状态，再重新连接；不要重试旧操作。",
              );
            }
            return;
          }
        }
        throw new Error(typeof detail === "string" ? detail : detail.message);
      }
      const data = (await response.json()) as ToolCallResult;
      setToolResults((current) => ({ ...current, [tool.name]: data }));
      if (isBrowserAdapter) {
        const elements = browserElementsFromResult(data);
        if (
          adapterStatus?.tool_policies[tool.name]?.effect === "state-write" ||
          adapterStatus?.tool_policies[tool.name]?.effect === "artifact-create"
        ) {
          setBrowserRefOptions([]);
        } else if (elements !== null) {
          setBrowserRefOptions(elements);
        }
      }
      const notice = noticeFromResult(data);
      if (notice) setToolNotices((current) => ({ ...current, [tool.name]: notice }));
      if (isBrowserAdapter && notice?.kind === "unknown-outcome") {
        markBrowserSessionTainted(
          "浏览器操作结果未知，临时会话已销毁。请先核对目标状态，再重新连接；不要重试旧操作。",
        );
        return;
      }
      if (boundWorkspace) await refreshBoundWorkspace(boundWorkspace.workspace_id);
      if (isBrowserAdapter) setBrowserRefreshKey((current) => current + 1);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "工具执行失败");
    } finally {
      setRunningTool(null);
      onConnectionChange?.();
    }
  }

  function invalidateCredentialSession() {
    ignoredRestoredSessionRef.current = sessionId ?? restoredSession?.session_id ?? null;
    setSessionId(null);
    setTools([]);
    setToolResults({});
    setToolNotices({});
    setFormValues({});
    setBrowserRefOptions([]);
    setError("");
    setState("idle");
    onConnectionChange?.();
  }

  function markBrowserSessionTainted(message: string) {
    ignoredRestoredSessionRef.current = sessionId ?? restoredSession?.session_id ?? null;
    setSessionId(null);
    setTools([]);
    setFormValues({});
    setBrowserRefOptions([]);
    setBrowserRefreshKey((current) => current + 1);
    setState("error");
    setError(message);
  }

  function handleWorkspaceBound(workspace: McpWorkspace | null) {
    setBoundWorkspace(workspace);
    if (workspacePolicy?.required) setCatalogConfigured(Boolean(workspace));
    onConnectionChange?.();
  }

  function handleConfigurationSaved(
    settings: Record<string, string | number | boolean>,
    bindings: Record<string, string>,
  ) {
    setCatalogSettings(settings);
    setCatalogBindings(bindings);
    onConnectionChange?.();
  }

  function showApproval(approval: McpCatalogApprovalRequest, onConfirmed: () => void) {
    approvalCallbackRef.current = onConfirmed;
    setPendingApproval(approval);
  }

  async function confirmApproval() {
    if (!pendingApproval) return;
    setApprovalBusy(true);
    setError("");
    try {
      const response = await fetch(
        `/api/mcp/catalog/${project.id}/approvals/${pendingApproval.approval_id}/confirm`,
        { method: "POST" },
      );
      if (!response.ok) {
        const detail = await readErrorDetail(response);
        if (typeof detail !== "string" && isCatalogOperationError(detail)) {
          const notice = noticeFromError(detail);
          if (notice && approvalToolRef.current) {
            const unknownBrowserOutcome =
              isBrowserAdapter && notice.kind === "unknown-outcome";
            setToolNotices((current) => ({
              ...current,
              [approvalToolRef.current as string]: notice,
            }));
            setPendingApproval(null);
            approvalCallbackRef.current = null;
            approvalToolRef.current = null;
            if (unknownBrowserOutcome) {
              markBrowserSessionTainted(
                "浏览器操作结果未知，临时会话已销毁。请先核对目标状态，再重新连接；不要重试旧操作。",
              );
              onConnectionChange?.();
            }
            return;
          }
        }
        throw new Error(typeof detail === "string" ? detail : detail.message);
      }
      const data = (await response.json()) as ToolCallResult;
      if (approvalToolRef.current) {
        const toolName = approvalToolRef.current;
        setToolResults((current) => ({ ...current, [toolName]: data }));
        if (isBrowserAdapter) {
          setBrowserRefOptions([]);
        }
        const notice = noticeFromResult(data);
        if (notice) setToolNotices((current) => ({ ...current, [toolName]: notice }));
        if (isBrowserAdapter && notice?.kind === "unknown-outcome") {
          markBrowserSessionTainted(
            "浏览器操作结果未知，临时会话已销毁。请先核对目标状态，再重新连接；不要重试旧操作。",
          );
          setPendingApproval(null);
          approvalCallbackRef.current = null;
          approvalToolRef.current = null;
          onConnectionChange?.();
          return;
        }
      }
      if (isBrowserAdapter) setBrowserRefreshKey((current) => current + 1);
      setPendingApproval(null);
      approvalCallbackRef.current?.();
      approvalCallbackRef.current = null;
      approvalToolRef.current = null;
    } catch (exc) {
      setPendingApproval(null);
      approvalCallbackRef.current = null;
      approvalToolRef.current = null;
      setError(
        exc instanceof Error
          ? `${exc.message} 请重新发起预览，不要重复提交旧审批。`
          : "确认操作失败，请重新发起预览。",
      );
    } finally {
      setApprovalBusy(false);
    }
  }

  async function cancelApproval() {
    if (!pendingApproval) return;
    await fetch(
      `/api/mcp/catalog/${project.id}/approvals/${pendingApproval.approval_id}`,
      { method: "DELETE" },
    ).catch(() => undefined);
    setPendingApproval(null);
    approvalCallbackRef.current = null;
    approvalToolRef.current = null;
  }

  async function refreshBoundWorkspace(workspaceId: string) {
    const response = await fetch(
      `/api/mcp/catalog/${project.id}/workspaces/${workspaceId}`,
    );
    if (response.ok) setBoundWorkspace((await response.json()) as McpWorkspace);
  }

  function renderField(tool: McpTool, key: string, property: JsonSchemaProperty) {
    const required = tool.inputSchema.required?.includes(key);
    const value =
      formValues[tool.name]?.[key] ?? defaultFieldValue(property);
    const label = property.title ?? key;
    const type = schemaType(property);

    if (property["x-modelmirror-input"] === "workspace-file") {
      return (
        <label className="block rounded-lg border border-white/10 bg-white/[0.04] p-3" key={key}>
          <span className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-200">
            {label}
            {required ? <span className="text-hire-100">必填</span> : null}
          </span>
          <span className="mt-1 block text-xs leading-5 text-slate-500">
            只能选择已封存工作区中的文件，不能输入宿主路径或 URI。
          </span>
          <select
            className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50"
            onChange={(event) => updateField(tool.name, key, event.target.value)}
            value={value || boundWorkspace?.files[0]?.file_id || ""}
          >
            {boundWorkspace?.files.map((file) => (
              <option key={file.file_id} value={file.file_id}>{file.relative_path}</option>
            ))}
          </select>
        </label>
      );
    }

    if (property["x-modelmirror-input"] === "workspace-directory") {
      const directories = Array.from(
        new Set(
          (boundWorkspace?.files ?? []).flatMap((file) => {
            const parts = file.relative_path.split("/").slice(0, -1);
            return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
          }),
        ),
      ).sort();
      return (
        <label className="block rounded-lg border border-white/10 bg-white/[0.04] p-3" key={key}>
          <span className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-200">
            {label}
            {required ? <span className="text-hire-100">必填</span> : null}
          </span>
          <span className="mt-1 block text-xs leading-5 text-slate-500">
            只能选择已封存工作区内的目录，不接受手工路径。
          </span>
          <select
            className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50"
            onChange={(event) => updateField(tool.name, key, event.target.value)}
            value={value}
          >
            <option value="">工作区根目录</option>
            {directories.map((directory) => (
              <option key={directory} value={directory}>{directory}</option>
            ))}
          </select>
        </label>
      );
    }

    if (property["x-modelmirror-input"] === "artifact-name") {
      return (
        <label className="block rounded-lg border border-white/10 bg-white/[0.04] p-3" key={key}>
          <span className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-200">
            {label}
            {required ? <span className="text-hire-100">必填</span> : null}
          </span>
          <span className="mt-1 block text-xs leading-5 text-slate-500">
            仅填写产物文件名；斜杠、反斜杠和 URI 分隔符会被拒绝。
          </span>
          <input
            autoComplete="off"
            className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50"
            maxLength={120}
            onChange={(event) =>
              updateField(tool.name, key, event.target.value.replace(/[\\/:]/g, ""))
            }
            pattern="[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff._ -]{0,119}"
            spellCheck={false}
            type="text"
            value={value}
          />
        </label>
      );
    }

    if (property["x-modelmirror-input"] === "browser-url") {
      return (
        <label className="block rounded-lg border border-cyan-300/15 bg-cyan-300/[0.04] p-3" key={key}>
          <span className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-200">
            {label}
            {required ? <span className="text-hire-100">必填</span> : null}
          </span>
          <span className="mt-1 block text-xs leading-5 text-slate-400">
            仅填写不含 Token、API Key 或签名参数的公网 HTTP/HTTPS 地址；只允许 80/443 端口，DNS 固定后连接，跨 origin 请求与重定向直接拒绝。
          </span>
          <input
            autoCapitalize="none"
            autoComplete="off"
            className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-300/50 focus-visible:ring-2 focus-visible:ring-cyan-300/30"
            inputMode="url"
            maxLength={2048}
            onChange={(event) => updateField(tool.name, key, event.target.value.trim())}
            placeholder="https://example.com/path"
            spellCheck={false}
            type="url"
            value={value}
          />
        </label>
      );
    }

    if (property["x-modelmirror-input"] === "browser-ref") {
      const selectedValue = browserRefOptions.some((option) => option.ref === value)
        ? value
        : "";
      return (
        <label className="block rounded-lg border border-cyan-300/15 bg-cyan-300/[0.04] p-3" key={key}>
          <span className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-200">
            {label}
            {required ? <span className="text-hire-100">必填</span> : null}
          </span>
          <span className="mt-1 block text-xs leading-5 text-slate-400">
            只能选择当前会话最新页面快照返回的元素；页面操作后需重新执行快照。
          </span>
          <select
            className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 font-mono text-sm text-white outline-none transition focus:border-cyan-300/50 focus-visible:ring-2 focus-visible:ring-cyan-300/30"
            disabled={browserRefOptions.length === 0}
            onChange={(event) => updateField(tool.name, key, event.target.value)}
            value={selectedValue}
          >
            <option value="">
              {browserRefOptions.length === 0 ? "请先执行页面快照" : "选择页面元素"}
            </option>
            {browserRefOptions.map((option) => (
              <option key={option.ref} value={option.ref}>
                {option.role} · {option.label} · {option.ref}
              </option>
            ))}
          </select>
        </label>
      );
    }

    return (
      <label className="block rounded-lg border border-white/10 bg-white/[0.04] p-3" key={key}>
        <span className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-200">
          {label}
          {required ? <span className="text-hire-100">必填</span> : null}
        </span>
        {property.description ? (
          <span className="mt-1 block text-xs leading-5 text-slate-500">
            {property.description}
          </span>
        ) : null}

        {property.enum?.length ? (
          <select
            className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50"
            onChange={(event) => updateField(tool.name, key, event.target.value)}
            value={value}
          >
            {property.enum.map((option) => (
              <option key={String(option)} value={String(option)}>
                {String(option)}
              </option>
            ))}
          </select>
        ) : type === "boolean" ? (
          <select
            className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50"
            onChange={(event) => updateField(tool.name, key, event.target.value)}
            value={value}
          >
            <option value="false">false</option>
            <option value="true">true</option>
          </select>
        ) : type === "array" || type === "object" ? (
          <textarea
            className="mt-2 min-h-20 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 font-mono text-xs text-white outline-none transition focus:border-brand-300/50"
            onChange={(event) => updateField(tool.name, key, event.target.value)}
            value={value}
          />
        ) : (
          <input
            className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50"
            onChange={(event) => updateField(tool.name, key, event.target.value)}
            type={type === "number" || type === "integer" ? "number" : "text"}
            value={value}
          />
        )}
      </label>
    );
  }

  return (
    <article
      className={`group relative flex h-full w-full flex-col rounded-lg p-5 shadow-prism transition duration-200 ${
        isReadyProductCard ? "min-h-[360px]" : "min-h-[280px]"
      } ${
        isReadyProductCard && isWorkbenchOpen ? "" : "isolate overflow-hidden"
      } border border-white/10 bg-ink-950/78 hover:border-hire-300/40 hover:bg-surface-900/92`}
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_12%,rgba(251,146,60,0.16),transparent_34%),radial-gradient(circle_at_82%_82%,rgba(36,217,255,0.13),transparent_36%)] opacity-80" />
      {isReadyProductCard ? (
        <>
          <div className="relative flex items-start justify-between gap-4">
            <div className="flex min-w-0 items-start gap-3">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-brand-300/20 bg-brand-300/[0.07] text-lg font-semibold text-brand-100">
                {project.category.slice(0, 1)}
              </span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full border border-brand-300/25 bg-brand-300/[0.08] px-2.5 py-1 text-xs font-semibold text-brand-100">
                    {project.category}
                  </span>
                  <span className="rounded-full border border-emerald-300/25 bg-emerald-300/[0.08] px-2.5 py-1 text-xs font-semibold text-emerald-100">
                    {isConnected ? "已连接" : "可用"}
                  </span>
                </div>
                <h2 className="mt-3 line-clamp-2 text-xl font-semibold leading-7 text-white">
                  {project.name}
                </h2>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-400">
                  <a
                    className="underline-offset-4 transition hover:text-brand-100 hover:underline"
                    href={project.repoUrl}
                    rel="noreferrer"
                    target="_blank"
                  >
                    {project.repoName}
                  </a>
                  {project.stars > 0 ? (
                    <>
                      <span aria-hidden="true">·</span>
                      <span className="inline-flex items-center gap-1.5 text-slate-300">
                        <Star aria-hidden="true" className="h-3.5 w-3.5 text-hire-200" strokeWidth={2} />
                        {formatStars(project.stars)} stars
                      </span>
                    </>
                  ) : null}
                </div>
              </div>
            </div>
          </div>

          <p className="relative mt-5 line-clamp-3 text-sm leading-6 text-slate-200">
            {readyCardCopy.description}
          </p>

          <div className="relative mt-5 border-y border-white/10 py-4">
            <p className="text-xs font-semibold text-slate-400">主要能力</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {readyCardCopy.tags.map((tag) => (
                <span
                  className="rounded-full border border-brand-300/20 bg-brand-300/[0.07] px-3 py-1.5 text-xs font-medium text-brand-50"
                  key={tag}
                >
                  {tag}
                </span>
              ))}
            </div>
          </div>

          <div className="relative mt-4 flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.035] px-4 py-3 text-xs text-slate-300">
            <Settings2 aria-hidden="true" className="h-4 w-4 shrink-0 text-brand-100" strokeWidth={1.8} />
            <span>{readyCardCopy.connectionNote}</span>
          </div>

          <div className="relative mt-auto flex flex-wrap gap-2 pt-5">
            <button
              className="min-h-11 flex-1 rounded-full bg-brand-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-brand-200"
              onClick={() => setIsWorkbenchOpen(true)}
              ref={workbenchTriggerRef}
              type="button"
            >
              <Wrench aria-hidden="true" className="mr-2 inline h-4 w-4" strokeWidth={2} />
              打开工具台
            </button>
            <button
              className="min-h-11 rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/30 hover:bg-hire-300/[0.08] hover:text-hire-100"
              onClick={() => setIsInstallOpen(true)}
              type="button"
            >
              配置与使用
            </button>
          </div>
        </>
      ) : (
        <>
      <div className="relative flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.06] text-lg font-semibold text-white">
            {project.category.slice(0, 1)}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap gap-2">
              <span className="rounded-full border border-brand-300/30 bg-brand-300/10 px-2.5 py-1 text-xs font-semibold text-brand-100">
                {project.category}
              </span>
              <span
                className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${
                  availability === "adapting"
                    ? "border-cyan-300/25 bg-cyan-300/10 text-cyan-100"
                    : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                }`}
              >
                {unavailableStatusLabel}
              </span>
            </div>
            <h2 className="mt-3 line-clamp-2 text-xl font-semibold leading-7 text-white">
              {project.name}
            </h2>
            <a
              className="mt-1 inline-flex text-xs text-slate-400 underline-offset-4 transition hover:text-brand-100 hover:underline"
              href={project.repoUrl}
              rel="noreferrer"
              target="_blank"
            >
              {project.repoName}
            </a>
          </div>
        </div>
        {project.stars > 0 ? (
          <span className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-xs font-semibold text-slate-200">
            {formatStars(project.stars)} stars
          </span>
        ) : (
          null
        )}
      </div>

      <p className="relative mt-4 line-clamp-2 text-sm leading-6 text-slate-300">
        {project.description}
      </p>

      <div
        className={`relative mt-4 rounded-lg border px-4 py-3 ${
          availability === "adapting"
            ? "border-cyan-300/20 bg-cyan-300/[0.06]"
            : "border-amber-300/20 bg-amber-300/[0.06]"
        }`}
      >
        <p className="text-xs font-semibold text-slate-200">{unavailableStatusLabel}</p>
        <p className="mt-1 text-xs leading-5 text-slate-400">{unavailableReason}</p>
        {project.requirements.length > 0 ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {project.requirements.slice(0, 3).map((requirement) => (
              <span
                className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-slate-300"
                key={requirement}
              >
                {mcpRequirementLabels[requirement]}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {remoteReviewEntryEnabled ? (
        <button
          aria-haspopup="dialog"
          className="relative mt-auto min-h-11 w-full rounded-full border border-cyan-300/30 bg-cyan-300/10 px-4 py-2 text-sm font-semibold text-cyan-100 transition hover:border-cyan-200/50 hover:bg-cyan-300/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200"
          onClick={() => setIsRemoteReviewOpen(true)}
          type="button"
        >
          认证与复核
        </button>
      ) : (
        <button
          className="relative mt-auto min-h-11 w-full cursor-not-allowed rounded-full border border-white/10 bg-white/[0.035] px-4 py-2 text-sm font-semibold text-slate-500"
          disabled
          type="button"
        >
          {unavailableStatusLabel}
        </button>
      )}
        </>
      )}

      {isReadyProductCard && isWorkbenchOpen ? (
        <div
          aria-labelledby={isReadyProductCard ? `mcp-workbench-${project.id}` : undefined}
          aria-modal={isReadyProductCard ? "true" : undefined}
          className={
            isReadyProductCard
              ? "fixed inset-0 z-[70] flex items-end justify-center bg-slate-950/80 p-0 backdrop-blur-sm sm:items-center sm:p-4"
              : "contents"
          }
          onMouseDown={(event) => {
            if (isReadyProductCard && event.target === event.currentTarget) closeWorkbench();
          }}
          role={isReadyProductCard ? "dialog" : undefined}
          ref={isReadyProductCard ? workbenchDialogRef : undefined}
        >
          <section
            className={
              isReadyProductCard
                ? "flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-t-xl border border-white/10 bg-ink-950 sm:rounded-xl"
                : "contents"
            }
            onMouseDown={(event) => {
              if (isReadyProductCard) event.stopPropagation();
            }}
          >
            {isReadyProductCard ? (
              <header className="flex shrink-0 items-start justify-between gap-4 border-b border-white/10 bg-surface-900/95 px-4 py-4 sm:px-6">
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-brand-100">MCP 工具台</p>
                  <h2 className="mt-1 truncate text-xl font-semibold text-white" id={`mcp-workbench-${project.id}`}>
                    {project.name}
                  </h2>
                  <p className="mt-1 text-xs text-slate-400">
                    {isConnected ? "连接已建立，可选择工具执行" : "完成配置并建立连接后即可使用工具"}
                  </p>
                </div>
                <button
                  aria-label="关闭工具台"
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/[0.04] text-slate-200 transition hover:bg-white/[0.09] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-200"
                  onClick={closeWorkbench}
                  ref={workbenchCloseRef}
                  type="button"
                >
                  <X aria-hidden="true" className="h-5 w-5" />
                </button>
              </header>
            ) : null}
            <div className={isReadyProductCard ? "min-h-0 flex-1 overflow-y-auto px-4 pb-6 sm:px-6" : "contents"}>
      {!isReadyProductCard && wave === 5 ? (
        <section
          aria-label="数据库安全与验证状态"
          className="relative mt-3 rounded-lg border border-emerald-300/20 bg-emerald-300/[0.055] p-3 text-xs"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold text-emerald-100">数据库安全状态</h3>
            <div className="flex flex-wrap gap-2">
              <span
                className={`rounded-full border px-2.5 py-1 font-semibold ${
                  availability === "blocked"
                    ? "border-rose-300/25 bg-rose-300/[0.08] text-rose-100"
                    : "border-emerald-300/25 bg-emerald-300/[0.08] text-emerald-100"
                }`}
              >
                {availability === "blocked" ? "连接关闭" : "只读连接"}
              </span>
              <span className="rounded-full border border-slate-300/20 bg-slate-300/[0.06] px-2.5 py-1 font-semibold text-slate-200">
                写入工具关闭
              </span>
            </div>
          </div>
          <dl className="mt-3 grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg bg-black/15 p-2.5">
              <dt className="text-slate-400">传输通道</dt>
              <dd aria-live="polite" className="mt-1 font-semibold text-slate-100">
                {availability === "blocked"
                  ? "未启动，连接入口关闭"
                  : state === "connecting"
                    ? "正在建立隔离 stdio"
                    : state === "connected" || adapterStatus?.connected
                      ? "隔离 stdio 已连接"
                      : state === "error"
                        ? "连接失败"
                        : "尚未连接"}
              </dd>
            </div>
            <div className="rounded-lg bg-black/15 p-2.5">
              <dt className="text-slate-400">数据源验证</dt>
              <dd
                aria-live="polite"
                className={`mt-1 font-semibold ${databasePreflightCopy[databasePreflight].className}`}
              >
                {databasePreflightCopy[databasePreflight].label}
              </dd>
            </div>
          </dl>
          {databasePolicy ? (
            <p className="mt-2 leading-5 text-slate-400">
              单次查询默认最多 {databasePolicy.max_rows_default} 行，硬上限 {databasePolicy.max_rows_hard} 行，语句超时 {databasePolicy.statement_timeout_seconds} 秒
              {databasePolicy.tls_required ? "；远程连接强制严格 TLS 校验。" : "；本地封存数据文件断网读取。"}
            </p>
          ) : (
            <p className="mt-2 leading-5 text-slate-400">
              当前条目未达到数据库运行门槛，不会启动连接或收集凭据。
            </p>
          )}
        </section>
      ) : null}

      {!isReadyProductCard && wave === 6 ? (
        <section
          aria-label="有状态 SaaS 账号与工具策略"
          className="relative mt-3 rounded-lg border border-violet-300/20 bg-violet-300/[0.055] p-3 text-xs"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold text-violet-100">账号与写入安全状态</h3>
            {availability === "blocked" ? (
              <span className="rounded-full border border-rose-300/25 bg-rose-300/[0.08] px-2.5 py-1 font-semibold text-rose-100">
                所有入口关闭
              </span>
            ) : (
              <div className="flex flex-wrap gap-2">
                <span className="rounded-full border border-emerald-300/25 bg-emerald-300/[0.08] px-2.5 py-1 font-semibold text-emerald-100">
                  只读直接执行
                </span>
                <span className="rounded-full border border-amber-300/25 bg-amber-300/[0.08] px-2.5 py-1 font-semibold text-amber-100">
                  写入 · 预览并确认
                </span>
              </div>
            )}
          </div>
          <dl className="mt-3 grid gap-2 sm:grid-cols-3">
            <div className="rounded-lg bg-black/15 p-2.5">
              <dt className="text-slate-400">目录功能门禁</dt>
              <dd
                aria-live="polite"
                className={`mt-1 font-semibold ${
                  availability === "blocked" || !statefulSaasGateEnabled
                    ? "text-rose-100"
                    : "text-emerald-100"
                }`}
              >
                {availability === "blocked"
                  ? "适配受阻，配置与工具入口关闭"
                  : statefulSaasGateEnabled
                    ? "有状态 SaaS 门禁已开启"
                    : "有状态 SaaS 总开关未开启"}
              </dd>
            </div>
            <div className="rounded-lg bg-black/15 p-2.5">
              <dt className="text-slate-400">账号绑定</dt>
              <dd
                aria-live="polite"
                className={`mt-1 font-semibold ${
                  saasAccountStatusCopy[adapterStatus?.account_status ?? "not-applicable"].className
                }`}
              >
                {saasAccountStatusCopy[adapterStatus?.account_status ?? "not-applicable"].label}
              </dd>
            </div>
            <div className="rounded-lg bg-black/15 p-2.5">
              <dt className="text-slate-400">账号预检</dt>
              <dd
                aria-live="polite"
                className={`mt-1 font-semibold ${saasPreflightCopy[databasePreflight].className}`}
              >
                {saasPreflightCopy[databasePreflight].label}
              </dd>
            </div>
          </dl>
          {adapterStatus?.saas_policy ? (
            <p className="mt-2 leading-5 text-slate-400">
              固定主机：{adapterStatus.saas_policy.fixed_hosts.join("、")}；每分钟最多 {adapterStatus.saas_policy.rate_limit_per_minute} 次、并发 {adapterStatus.saas_policy.max_concurrent_calls}。限流和结果未知时写入不自动重试。
            </p>
          ) : (
            <p className="mt-2 leading-5 text-slate-400">
              当前条目没有可执行账号契约；不会收集凭据、资源 ID 或工具参数。
            </p>
          )}
        </section>
      ) : null}

      {wave === 7 ? (
        <McpBrowserPanel
          availability={availability}
          compact={isReadyProductCard}
          connected={state === "connected" || adapterStatus?.connected === true}
          policy={browserPolicy}
          preflightStatus={databasePreflight}
          projectId={project.id}
          refreshKey={browserRefreshKey}
        />
      ) : null}

      {!isReadyProductCard && adapterStatus?.executable && adapterStatus.runtime_image ? (
        <div className="relative mt-3 rounded-lg border border-emerald-300/20 bg-emerald-300/[0.06] p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-semibold text-emerald-100">已验证运行隔离</p>
            <span className="rounded-full border border-emerald-300/25 px-2 py-1 text-emerald-100">
              {adapterStatus.adapter_version}
            </span>
          </div>
          <div className="mt-2 grid gap-2 text-slate-300 sm:grid-cols-2">
            <p>网络：{formatMcpIsolation(adapterStatus.network_policy)}</p>
            <p>文件：{formatMcpIsolation(adapterStatus.filesystem_policy)}</p>
          </div>
          <p className="mt-2 break-all text-slate-400">
            固定运行镜像：{adapterStatus.runtime_image}
          </p>
        </div>
      ) : null}

      {workspacePolicy && canConnect ? (
        <McpWorkspacePanel
          boundWorkspaceId={adapterStatus?.workspace_id ?? null}
          configurationSettings={catalogSettings}
          credentialBindings={catalogBindings}
          onApprovalRequired={showApproval}
          onBound={handleWorkspaceBound}
          policy={workspacePolicy}
          projectId={project.id}
          refreshKey={boundWorkspace?.artifacts.map((item) => item.artifact_id).join(",") ?? ""}
        />
      ) : null}

      {canConfigureCatalog && needsCatalogConfiguration && adapterStatus ? (
        <McpCredentialPanel
          connectionPending={state === "connecting"}
          credentialFields={adapterStatus.credential_fields}
          disabled={state === "connected" || state === "connecting"}
          initialBindings={adapterStatus.credential_bindings}
          initialSettings={adapterStatus.configuration_values}
          initiallyConfigured={adapterStatus.configured}
          credentialVerification={adapterStatus.credential_verification}
          accountStatus={adapterStatus.account_status}
          databasePreflightStatus={adapterStatus.preflight_status}
          mode={wave === 5 ? "database" : wave === 6 ? "saas" : "service"}
          onConfigurationSaved={handleConfigurationSaved}
          onConfigured={setCatalogConfigured}
          onSessionInvalidated={invalidateCredentialSession}
          projectId={project.id}
          saasPolicy={adapterStatus.saas_policy}
          settingFields={adapterStatus.setting_fields}
          workspaceId={boundWorkspace?.workspace_id ?? null}
        />
      ) : null}

      <div className="relative mt-auto flex flex-wrap items-center gap-2 pt-5">
        {canConnect ? (
          <button
            className={`min-h-11 rounded-full px-4 py-2 text-sm font-semibold text-ink-950 transition duration-200 disabled:cursor-not-allowed disabled:opacity-45 ${
              isPlaywrightCard
                ? "bg-hire-300 hover:bg-hire-200"
                : "bg-brand-300 shadow-[0_0_24px_rgba(34,211,238,0.18)] hover:bg-brand-200"
            }`}
            disabled={
              state === "connecting" ||
              state === "connected" ||
              !canStartConnection
            }
            onClick={() => void connect()}
            type="button"
          >
            {isPlaywrightCard && state !== "connecting" && state !== "connected" ? (
              <Link2 aria-hidden="true" className="mr-2 inline h-4 w-4" strokeWidth={2} />
            ) : null}
            {state === "connecting"
              ? isBrowserAdapter ? "正在创建临时会话" : "连接中..."
              : state === "connected"
                ? "传输已连接"
                : workspacePolicy?.required && !boundWorkspace
                  ? "先绑定工作区"
                  : needsCatalogConfiguration && !catalogConfigured
                    ? "先保存连接配置"
                  : isPlaywrightCard
                    ? "连接 Playwright"
                    : isBrowserAdapter ? "连接临时浏览器" : "连接 Server"}
          </button>
        ) : (
          <button
            className="min-h-11 cursor-not-allowed rounded-full border border-amber-300/25 bg-amber-300/10 px-4 py-2 text-sm font-semibold text-amber-100 opacity-75"
            disabled
            type="button"
          >
            {!adapterStatus
              ? "同步服务端状态..."
              : isStatefulSaas && !statefulSaasGateEnabled
                ? "SaaS 门禁未开启"
              : !adapterStatus.feature_enabled
                ? "项目开关未开启"
                : "暂不可连接"}
          </button>
        )}
        {state === "connected" ? (
          <button
            className="min-h-11 rounded-full border border-rose-300/30 bg-rose-300/10 px-4 py-2 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/15"
            onClick={() => void disconnect()}
            type="button"
          >
            {isBrowserAdapter ? "结束临时会话" : "断开连接"}
          </button>
        ) : null}
        {canInstall && !isPlaywrightCard ? (
          <button
            className={`min-h-11 rounded-full border px-4 py-2 text-sm font-semibold transition duration-200 disabled:cursor-not-allowed disabled:opacity-60 ${
              installState === "installed"
                ? "border-emerald-300/35 bg-emerald-300/12 text-emerald-100"
                : "border-white/10 bg-white/[0.055] text-slate-100 hover:border-brand-300/35 hover:bg-brand-300/10 hover:text-brand-100"
            }`}
            disabled={
              installState === "checking" ||
              installState === "installing" ||
              installState === "installed"
            }
            onClick={() => void installProject()}
            type="button"
          >
            {installState === "checking"
              ? "检查中..."
              : installState === "installing"
                ? "安装中..."
                : installState === "installed"
                  ? "已安装"
                  : "安装 Server"}
          </button>
        ) : null}
        <button
          className={`min-h-11 rounded-full border px-3 py-2 text-xs font-semibold transition duration-200 ${
            isPlaywrightCard
              ? "border-cyan-300/25 bg-cyan-300/[0.06] text-cyan-100 hover:bg-cyan-300/10"
              : "border-white/10 bg-white/[0.04] text-slate-300 hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
          }`}
          onClick={() => setIsInstallOpen(true)}
          type="button"
        >
          {isPlaywrightCard ? "配置与使用" : "中文配置与使用"}
        </button>
      </div>

      {!isReadyProductCard ? (
      <div className="relative mt-4 rounded-lg border border-white/10 bg-slate-950/55 p-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs font-semibold text-slate-300">
              {canConnect ? "受控连接" : "适配计划"}
            </p>
            <code
              className={`mt-2 block break-all text-xs ${
                canConnect ? "text-brand-100" : "text-amber-100"
              }`}
            >
              {commandPreview}
            </code>
          </div>
          {restoredSession ? (
            <span className="w-fit rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-1 text-xs font-semibold text-slate-300">
              已连接 {Math.max(0, Math.floor(restoredSession.uptime_seconds))}s
            </span>
          ) : null}
        </div>
      </div>
      ) : null}

      {installError ? (
        <div className="relative mt-4 rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-sm leading-6 text-amber-50">
          MCP 安装提示：{installError}
        </div>
      ) : null}

      {error ? (
        <div className="relative mt-4 rounded-lg border border-rose-300/25 bg-rose-300/10 p-3 text-sm leading-6 text-rose-100">
          {error}
        </div>
      ) : null}

      {state === "connected" && canConnect ? (
        <div className="relative mt-5 space-y-4 border-t border-white/10 pt-4">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-white">工具清单</h3>
            <span className="rounded-full border border-brand-300/25 bg-brand-300/10 px-2.5 py-1 text-xs font-semibold text-brand-100">
              {tools.length} 个工具
            </span>
          </div>
          {tools.length === 0 ? (
            <p className="rounded-lg border border-white/10 bg-white/[0.04] p-3 text-sm text-slate-400">
              该 MCP Server 暂未暴露工具。
            </p>
          ) : (
            tools.map((tool) => {
              const properties = tool.inputSchema.properties ?? {};
              const markdown = contentToMarkdown(toolResults[tool.name] ?? null);
              const toolPolicy = adapterStatus?.tool_policies[tool.name];
              const isApprovedWrite =
                toolPolicy?.effect === "state-write" && toolPolicy.requires_approval;
              const policyClosed = Boolean(
                toolPolicy?.sensitive ||
                toolPolicy?.terminal ||
                ((isStatefulSaas || isBrowserAdapter) &&
                  (!toolPolicy ||
                    (toolPolicy.effect === "state-write" && !toolPolicy.requires_approval))),
              );
              const notice = toolNotices[tool.name];
              return (
                <div className="min-w-0 rounded-lg border border-white/10 bg-white/[0.045] p-4" key={tool.name}>
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <p className="font-semibold text-white">{tool.title ?? tool.name}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        <p className="text-xs text-brand-100">{tool.name}</p>
                        {toolPolicy?.read_only || toolPolicy?.effect === "read" ? (
                          <span className="rounded-full border border-emerald-300/25 bg-emerald-300/[0.08] px-2 py-0.5 text-[11px] font-semibold text-emerald-100">
                            只读
                          </span>
                        ) : null}
                        {isApprovedWrite ? (
                          <span className="rounded-full border border-amber-300/25 bg-amber-300/[0.08] px-2 py-0.5 text-[11px] font-semibold text-amber-100">
                            {isBrowserAdapter ? "页面操作 · 需确认" : "写入 · 需确认"}
                          </span>
                        ) : null}
                        {toolPolicy?.effect === "artifact-create" ? (
                          <span className="rounded-full border border-cyan-300/25 bg-cyan-300/[0.08] px-2 py-0.5 text-[11px] font-semibold text-cyan-100">
                            生成产物
                          </span>
                        ) : null}
                        {policyClosed ? (
                          <span className="rounded-full border border-rose-300/25 bg-rose-300/[0.08] px-2 py-0.5 text-[11px] font-semibold text-rose-100">
                            策略关闭
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <button
                      className="min-h-11 w-fit rounded-full bg-hire-300 px-4 py-2 text-xs font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={runningTool === tool.name || policyClosed}
                      onClick={() => void callTool(tool)}
                      type="button"
                    >
                      {runningTool === tool.name
                        ? "执行中..."
                        : policyClosed
                          ? "工具已关闭"
                          : isApprovedWrite
                            ? isBrowserAdapter ? "预览浏览器操作" : "预览并确认"
                            : "执行"}
                    </button>
                  </div>
                  {tool.description ? (
                    <p className="mt-2 text-sm leading-6 text-slate-400">
                      {tool.description}
                    </p>
                  ) : null}
                  {Object.keys(properties).length > 0 ? (
                    <div className="mt-3 grid gap-3">
                      {Object.entries(properties).map(([key, property]) =>
                        renderField(tool, key, property),
                      )}
                    </div>
                  ) : (
                    <p className="mt-3 rounded-lg border border-white/10 bg-white/[0.04] p-3 text-xs text-slate-500">
                      这个工具不需要参数。
                    </p>
                  )}
                  {notice ? (
                    <div
                      aria-live="polite"
                      className={`mt-3 rounded-lg border p-3 text-xs leading-5 ${
                        notice.kind === "unknown-outcome"
                          ? "border-rose-300/25 bg-rose-300/[0.08] text-rose-100"
                          : notice.kind === "rate-limited"
                            ? "border-amber-300/25 bg-amber-300/[0.08] text-amber-100"
                            : notice.kind === "idempotent-replay"
                              ? "border-cyan-300/25 bg-cyan-300/[0.08] text-cyan-100"
                              : "border-emerald-300/25 bg-emerald-300/[0.08] text-emerald-100"
                      }`}
                      role="status"
                    >
                      <p className="font-semibold">
                        {notice.kind === "unknown-outcome"
                          ? "操作结果未知 · 禁止自动重试"
                          : notice.kind === "rate-limited"
                            ? "上游限流 · 操作未自动重试"
                          : notice.kind === "idempotent-replay"
                              ? `幂等重放 · ${isBrowserAdapter ? "未重复执行" : "未重复写入"}`
                              : "操作已完成"}
                      </p>
                      <p className="mt-1 text-slate-300">{notice.message}</p>
                      {notice.retryAfterSeconds !== undefined ? (
                        <p className="mt-1 text-slate-400">建议等待 {notice.retryAfterSeconds} 秒后重新发起预览。</p>
                      ) : null}
                      {notice.idempotencyKey ? (
                        <p className="mt-1 font-mono text-slate-400">
                          幂等键：{shortIdempotencyKey(notice.idempotencyKey)}
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                  {markdown ? (
                    <div className="prose prose-invert mt-4 max-h-[32rem] min-w-0 max-w-none overflow-auto rounded-lg border border-white/10 bg-ink-950/70 p-4 [overflow-wrap:anywhere] prose-pre:max-w-full prose-pre:overflow-x-auto prose-pre:bg-slate-950 prose-code:break-words prose-code:text-brand-100 prose-table:block prose-table:max-w-full prose-table:overflow-x-auto">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {markdown}
                      </ReactMarkdown>
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      ) : null}
            </div>
          </section>
        </div>
      ) : null}

      {pendingApproval ? (
        <McpApprovalDialog
          approval={pendingApproval}
          busy={approvalBusy}
          onCancel={cancelApproval}
          onConfirm={confirmApproval}
        />
      ) : null}

      {remoteReviewEntryEnabled && isRemoteReviewOpen
        ? createPortal(
          <div
            aria-labelledby={`mcp-remote-review-${project.id}`}
            aria-modal="true"
            className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/78 p-4 backdrop-blur-sm"
            role="dialog"
          >
          <div className="surface-card max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg p-5 sm:p-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-semibold text-cyan-100">本地运维者功能</p>
                <h2
                  className="mt-1 text-xl font-semibold text-white"
                  id={`mcp-remote-review-${project.id}`}
                >
                  {project.name} · 认证与复核
                </h2>
                <p className="mt-1 text-xs leading-5 text-slate-400">
                  此流程只生成受控执行契约；R4A 不会激活或暴露 Runtime 工具。
                </p>
              </div>
              <button
                aria-label="关闭认证与复核"
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/[0.04] text-slate-200 transition hover:bg-white/[0.09] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200"
                onClick={() => setIsRemoteReviewOpen(false)}
                type="button"
              >
                <X aria-hidden="true" className="h-5 w-5" />
              </button>
            </div>
            {canConfigureCatalog && needsCatalogConfiguration && adapterStatus ? (
              <McpCredentialPanel
                connectionPending={state === "connecting"}
                credentialFields={adapterStatus.credential_fields}
                disabled={state === "connected" || state === "connecting"}
                initialBindings={adapterStatus.credential_bindings}
                initialSettings={adapterStatus.configuration_values}
                initiallyConfigured={adapterStatus.configured}
                credentialVerification={adapterStatus.credential_verification}
                accountStatus={adapterStatus.account_status}
                databasePreflightStatus={adapterStatus.preflight_status}
                mode="service"
                onConfigurationSaved={handleConfigurationSaved}
                onConfigured={setCatalogConfigured}
                onSessionInvalidated={invalidateCredentialSession}
                projectId={project.id}
                settingFields={adapterStatus.setting_fields}
              />
            ) : null}
            <McpCatalogRemotePanel projectId={project.id} />
          </div>
        </div>,
          document.body,
        )
        : null}

      {isInstallOpen ? (
        <div
          aria-labelledby={`mcp-guide-${project.id}`}
          aria-modal="true"
          className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/78 p-4 backdrop-blur-sm"
          role="dialog"
        >
          <div className="surface-card max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg p-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-semibold text-hire-100">
                  {isPlaywrightCard ? "配置与使用" : "中文配置与使用指引"}
                </p>
                <h2
                  className="mt-2 text-2xl font-semibold text-white"
                  id={`mcp-guide-${project.id}`}
                >
                  {project.name}
                </h2>
              </div>
              <button
                aria-label="关闭安装说明"
                className="min-h-11 rounded-full border border-white/10 bg-white/[0.06] px-3 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/10"
                onClick={() => setIsInstallOpen(false)}
                type="button"
              >
                关闭
              </button>
            </div>
            {isPlaywrightCard ? (
              <div className="mt-5 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.06] p-4">
                <p className="text-sm font-semibold text-cyan-100">临时匿名浏览器</p>
                <p className="mt-2 text-sm leading-6 text-slate-300">
                  连接后即可打开公开网页、读取页面并执行受控点击、填写与截图。会话结束后登录状态不会保留。
                </p>
              </div>
            ) : <div
              className={`mt-5 rounded-lg border p-4 ${
                canConnect
                  ? "border-emerald-300/25 bg-emerald-300/[0.08]"
                  : "border-amber-300/25 bg-amber-300/[0.08]"
              }`}
            >
              <p
                className={`text-sm font-semibold ${
                  canConnect ? "text-emerald-100" : "text-amber-100"
                }`}
              >
                {canConnect
                  ? `已通过生产验收 · ${mcpConnectionKindLabels[connectionKind]}`
                  : `${mcpAvailabilityLabels[availability]} · 第 ${wave} 批`}
              </p>
              <p className="mt-2 text-sm leading-6 text-slate-300">
                {project.installNote}
              </p>
              {!canConnect ? (
                <p className="mt-2 text-xs leading-5 text-amber-50/80">
                  本页不会要求输入 Token，也不会跳转到 OAuth 或外站登录页面。
                </p>
              ) : null}
            </div>}

            {!isPlaywrightCard && project.requirements.length > 0 ? (
              <section className="mt-5">
                <h3 className="text-sm font-semibold text-white">接入条件</h3>
                <div className="mt-2 flex flex-wrap gap-2">
                  {project.requirements.map((requirement) => (
                    <span
                      className="rounded-full border border-amber-300/20 bg-amber-300/10 px-2.5 py-1 text-xs font-medium text-amber-50"
                      key={requirement}
                    >
                      {mcpRequirementLabels[requirement]}
                    </span>
                  ))}
                </div>
              </section>
            ) : null}

            <section className="mt-5">
              <h3 className="text-sm font-semibold text-white">配置步骤</h3>
              <ol className="mt-3 space-y-3">
                {(isPlaywrightCard
                  ? [
                      "点击“连接 Playwright”创建临时浏览器会话。",
                      "选择浏览器工具，填写公开网页地址或页面操作参数。",
                      "截图会显示在会话与截图区域，可下载或清理。",
                    ]
                  : project.configGuide).map((step, index) => (
                  <li
                    className="flex gap-3 text-sm leading-6 text-slate-300"
                    key={step}
                  >
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-brand-300/25 bg-brand-300/10 text-xs font-semibold text-brand-100">
                      {index + 1}
                    </span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
            </section>

            <section className="mt-5 rounded-lg border border-white/10 bg-white/[0.04] p-4">
              <h3 className="text-sm font-semibold text-white">可以怎么用</h3>
              <ul className="mt-3 space-y-2 text-sm leading-6 text-slate-300">
                {(isPlaywrightCard
                  ? ["核对公开页面的标题、链接和关键文案", "在逐次确认后点击、填写并生成截图"]
                  : project.usageExamples).map((example) => (
                  <li className="flex gap-2" key={example}>
                    <span aria-hidden="true" className="text-brand-100">
                      •
                    </span>
                    <span>{example}</span>
                  </li>
                ))}
              </ul>
            </section>

            {canConnect && !isPlaywrightCard ? (
              <section className="mt-5 rounded-lg border border-emerald-300/20 bg-emerald-300/[0.07] p-4">
                <h3 className="text-sm font-semibold text-emerald-100">
                  服务端受控适配器
                </h3>
                <p className="mt-2 text-sm leading-6 text-slate-300">
                  {isBrowserAdapter
                    ? "启动命令、浏览器参数、代理、CDP、Header 与环境变量均由后端固定；导航 URL 只作为受控工具参数提交，并经过公网目标、DNS 与单一 Origin 策略校验。"
                    : "安装命令、启动参数和工作目录由后端按项目 ID 固定管理；浏览器不会提交命令、URL、Header 或环境变量。"}
                </p>
              </section>
            ) : null}
          </div>
        </div>
      ) : null}
    </article>
  );
}
