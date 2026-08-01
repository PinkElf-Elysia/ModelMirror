import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { McpProject } from "../data/mcpProjects";

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
}

interface InstalledMcpRecord {
  project_id: string;
}

interface McpServerCardProps {
  project: McpProject;
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

export default function McpServerCard({
  project,
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
  const [runningTool, setRunningTool] = useState<string | null>(null);
  const [isInstallOpen, setIsInstallOpen] = useState(false);
  const [installState, setInstallState] = useState<InstallState>("checking");
  const [installError, setInstallError] = useState("");

  const canConnect = Boolean(project.command?.length);
  const commandPreview = useMemo(
    () => project.command?.join(" ") ?? "该项目暂未提供本地 stdio 启动命令",
    [project.command],
  );

  useEffect(() => {
    let cancelled = false;

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
  }, [project.id]);

  useEffect(() => {
    if (!restoredSession || restoredSession.session_id === sessionId) return;
    setSessionId(restoredSession.session_id);
    setState("connected");
    setError("");
    void fetchTools(restoredSession.session_id);
  }, [restoredSession, sessionId]);

  async function readError(response: Response) {
    try {
      const data = (await response.json()) as { detail?: string; error?: string };
      return data.detail ?? data.error ?? response.statusText;
    } catch {
      return response.statusText;
    }
  }

  async function connect() {
    if (!project.command) return;
    setState("connecting");
    setError("");
    setTools([]);
    setToolResults({});
    try {
      const response = await fetch("/api/mcp/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_command: project.command }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const data = (await response.json()) as { session_id: string };
      setSessionId(data.session_id);

      await fetchTools(data.session_id);
      setState("connected");
      onConnectionChange?.();
    } catch (exc) {
      setState("error");
      setError(exc instanceof Error ? exc.message : "无法连接 MCP Server");
    }
  }

  async function fetchTools(nextSessionId: string) {
    const toolsResponse = await fetch(`/api/mcp/${nextSessionId}/tools`);
    if (!toolsResponse.ok) throw new Error(await readError(toolsResponse));
    const toolsData = (await toolsResponse.json()) as { tools: McpTool[] };
    setTools(toolsData.tools);
  }

  async function disconnect() {
    if (sessionId) {
      await fetch(`/api/mcp/${sessionId}`, { method: "DELETE" }).catch(() => undefined);
    }
    setSessionId(null);
    setTools([]);
    setToolResults({});
    setFormValues({});
    setError("");
    setState("idle");
    onConnectionChange?.();
  }

  async function installProject() {
    if (installState === "installing" || installState === "installed") return;
    setInstallState("installing");
    setInstallError("");
    try {
      const response = await fetch("/api/mcp/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: project.id,
          install_command: project.installCommand,
          server_command: project.command ?? null,
        }),
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
      const rawValue = values[key] ?? defaultFieldValue(property);
      const coerced = coerceFieldValue(property, rawValue);
      if (coerced !== undefined) args[key] = coerced;
    }
    return args;
  }

  async function callTool(tool: McpTool) {
    if (!sessionId) return;
    setRunningTool(tool.name);
    setError("");
    try {
      const response = await fetch(`/api/mcp/${sessionId}/call`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tool_name: tool.name,
          arguments: buildArguments(tool),
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const data = (await response.json()) as ToolCallResult;
      setToolResults((current) => ({ ...current, [tool.name]: data }));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "工具执行失败");
    } finally {
      setRunningTool(null);
    }
  }

  function renderField(tool: McpTool, key: string, property: JsonSchemaProperty) {
    const required = tool.inputSchema.required?.includes(key);
    const value =
      formValues[tool.name]?.[key] ?? defaultFieldValue(property);
    const label = property.title ?? key;
    const type = schemaType(property);

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
            className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50"
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
            className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50"
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
            className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50"
            onChange={(event) => updateField(tool.name, key, event.target.value)}
            type={type === "number" || type === "integer" ? "number" : "text"}
            value={value}
          />
        )}
      </label>
    );
  }

  return (
    <article className="group relative isolate flex min-h-[360px] flex-col overflow-hidden rounded-lg border border-white/10 bg-ink-950/78 p-5 shadow-prism transition duration-300 hover:-translate-y-1 hover:border-hire-300/40 hover:bg-surface-900/92">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_18%_12%,rgba(251,146,60,0.16),transparent_34%),radial-gradient(circle_at_82%_82%,rgba(36,217,255,0.13),transparent_36%)] opacity-80" />
      <div className="relative flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.06] text-lg font-semibold text-white">
            工
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap gap-2">
              <span className="rounded-full border border-brand-300/30 bg-brand-300/10 px-2.5 py-1 text-xs font-semibold text-brand-100">
                万能工具招领
              </span>
              <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2.5 py-1 text-xs font-semibold text-emerald-100">
                {canConnect ? "可原生连接" : "展示项目"}
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
        <span className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-xs font-semibold text-slate-200">
          {formatStars(project.stars)} stars
        </span>
      </div>

      <p className="relative mt-5 text-sm leading-6 text-slate-300">
        {project.description}
      </p>

      <div className="relative mt-5 rounded-lg border border-white/10 bg-white/[0.045] p-3">
        <p className="text-xs font-semibold text-slate-200">README 摘要</p>
        <p className="mt-2 line-clamp-4 text-xs leading-5 text-slate-400">
          {project.readmeSummary}
        </p>
      </div>

      <div className="relative mt-4 grid grid-cols-2 gap-3 text-sm">
        <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3">
          <p className="text-xs text-slate-400">主要语言</p>
          <p className="mt-1 font-semibold text-white">{project.language}</p>
        </div>
        <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3">
          <p className="text-xs text-slate-400">最近更新</p>
          <p className="mt-1 font-semibold text-white">{project.updatedAt}</p>
        </div>
      </div>

      <div className="relative mt-4 flex flex-wrap gap-2">
        {project.tags.map((tag) => (
          <span
            className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-xs font-medium text-slate-300"
            key={tag}
          >
            {tag}
          </span>
        ))}
      </div>

      <div className="relative mt-auto flex flex-wrap items-center gap-2 pt-5">
        <button
          className="rounded-full bg-brand-300 px-4 py-2 text-sm font-semibold text-ink-950 shadow-[0_0_24px_rgba(34,211,238,0.18)] transition duration-200 hover:bg-brand-200 disabled:cursor-not-allowed disabled:opacity-45"
          disabled={!canConnect || state === "connecting"}
          onClick={() => void connect()}
          type="button"
        >
          {state === "connecting" ? "连接中..." : state === "connected" ? "已连接" : "连接"}
        </button>
        {state === "connected" ? (
          <button
            className="rounded-full border border-rose-300/30 bg-rose-300/10 px-4 py-2 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/15"
            onClick={() => void disconnect()}
            type="button"
          >
            断开连接
          </button>
        ) : null}
        <button
          className={`rounded-full border px-4 py-2 text-sm font-semibold transition duration-200 disabled:cursor-not-allowed disabled:opacity-60 ${
            installState === "installed"
              ? "border-emerald-300/35 bg-emerald-300/12 text-emerald-100"
              : "border-white/10 bg-white/[0.055] text-slate-100 hover:border-brand-300/35 hover:bg-brand-300/10 hover:text-brand-100"
          }`}
          disabled={installState === "checking" || installState === "installing" || installState === "installed"}
          onClick={() => void installProject()}
          type="button"
        >
          {installState === "checking"
            ? "检查中..."
            : installState === "installing"
              ? "安装中..."
              : installState === "installed"
                ? "已安装"
                : "⚡ 安装"}
        </button>
        <button
          className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-semibold text-slate-300 transition duration-200 hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
          onClick={() => setIsInstallOpen(true)}
          type="button"
        >
          命令
        </button>
      </div>

      <div className="relative mt-4 rounded-lg border border-white/10 bg-slate-950/55 p-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs font-semibold text-slate-300">stdio 命令</p>
            <code className="mt-2 block break-all text-xs text-brand-100">
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

      {state === "connected" ? (
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
              return (
                <div className="rounded-lg border border-white/10 bg-white/[0.045] p-4" key={tool.name}>
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <p className="font-semibold text-white">{tool.title ?? tool.name}</p>
                      <p className="mt-1 text-xs text-brand-100">{tool.name}</p>
                    </div>
                    <button
                      className="w-fit rounded-full bg-hire-300 px-3 py-1.5 text-xs font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={runningTool === tool.name}
                      onClick={() => void callTool(tool)}
                      type="button"
                    >
                      {runningTool === tool.name ? "执行中..." : "执行"}
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
                  {markdown ? (
                    <div className="prose prose-invert mt-4 max-w-none rounded-lg border border-white/10 bg-ink-950/70 p-4 prose-pre:bg-slate-950 prose-code:text-brand-100">
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

      {isInstallOpen ? (
        <div
          aria-modal="true"
          className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/78 p-4 backdrop-blur-sm"
          role="dialog"
        >
          <div className="surface-card w-full max-w-2xl rounded-lg p-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-semibold text-hire-100">安装指令</p>
                <h2 className="mt-2 text-2xl font-semibold text-white">
                  {project.name}
                </h2>
              </div>
              <button
                aria-label="关闭安装说明"
                className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-sm font-semibold text-slate-200 transition hover:bg-white/10"
                onClick={() => setIsInstallOpen(false)}
                type="button"
              >
                关闭
              </button>
            </div>
            <p className="mt-4 text-sm leading-6 text-slate-300">
              {project.installNote}
            </p>
            <pre className="mt-4 max-h-72 overflow-auto rounded-lg border border-white/10 bg-slate-950/78 p-4 text-xs leading-5 text-brand-100">
              <code>{project.installCommand}</code>
            </pre>
          </div>
        </div>
      ) : null}
    </article>
  );
}
