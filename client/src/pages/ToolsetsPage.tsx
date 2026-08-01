import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import PageContainer from "../components/PageContainer";

type Transport = "stdio" | "streamable_http" | "legacy_sse";
type CredentialKind = "header" | "environment" | "provider_key" | "generic";
type ToolsetKind = "mcp" | "openapi" | "odata" | "builtin";
type ToolMemoryMode = "off" | "run" | "conversation";
type APIAuthType = "none" | "api_key" | "bearer" | "basic" | "oauth2_client_credentials";

interface APIAuthProfile {
  auth_type: APIAuthType;
  credential_id: string;
  api_key_name: string;
  api_key_location: "header" | "query";
  username_credential_id: string;
  password_credential_id: string;
  client_id_credential_id: string;
  client_secret_credential_id: string;
  token_url: string;
  scopes: string[];
}

interface CredentialSummary {
  credential_id: string;
  name: string;
  kind: CredentialKind;
  prefix: string;
  masked_value: string;
  status: string;
}

interface SecretBinding {
  name: string;
  credential_id: string;
}

interface ConnectionProfile {
  transport: Transport;
  command: string[];
  url: string;
  headers: SecretBinding[];
  environment: SecretBinding[];
  installed_project_id: string;
  working_directory: string;
  auto_start: boolean;
  auto_reconnect: boolean;
  reconnect_attempts: number;
  tool_prefix: string;
  network_policy: "public_only" | "trusted_private";
  timeout_seconds: number;
  api_base_url: string;
  api_source_url: string;
  api_source_label: string;
  api_spec_version: string;
  api_spec_hash: string;
  api_auth: APIAuthProfile;
  response_limit_bytes: number;
  redirect_limit: number;
  provider_id: string;
  provider_credential_id: string;
  provider_config: Record<string, unknown>;
}

interface ToolDefinition {
  original_name: string;
  alias: string;
  description: string;
  input_schema: Record<string, unknown>;
  default_arguments: Record<string, unknown>;
  enabled: boolean;
  order: number;
  schema_hash: string;
  execution: Record<string, unknown>;
  read_only: boolean;
  requires_approval: boolean;
  sensitive: boolean;
  terminal: boolean;
  memory_mode: ToolMemoryMode;
  parallel_safe: boolean;
  public_app_allowed: boolean;
  compatibility: "compatible" | "warning" | "breaking";
  compatibility_message: string;
}

interface ToolsetVersion {
  version: number;
  draft_revision: number;
  schema_hash: string;
  release_notes: string;
  published_at: number;
  tools: ToolDefinition[];
}

interface ToolsetDefinition {
  id: string;
  kind: ToolsetKind;
  name: string;
  description: string;
  tags: string[];
  privacy_policy: string;
  disclaimer: string;
  status: "draft" | "published" | "archived";
  revision: number;
  published_version: number | null;
  connection: ConnectionProfile;
  tools: ToolDefinition[];
  versions: ToolsetVersion[];
  runtime_status: string;
  runtime_session_id: string | null;
  runtime_error: string;
  import_warnings: string[];
  drift_report: {
    breaking?: string[];
    warnings?: string[];
    added?: string[];
    removed?: string[];
    compatible?: boolean;
  };
  updated_at: number;
}

interface InstalledMCPProject {
  project_id: string;
  server_command?: string[];
  install_type?: string;
  npm_package?: string;
}

interface BuiltinProvider {
  id: string;
  title: string;
  description: string;
  credential_required: boolean;
  credential_kind: CredentialKind | null;
  instance_creatable: boolean;
  runtime_binding?: string;
  singleton?: boolean;
  default_toolset_id?: string | null;
  configuration_status?: "ready" | "credential_required" | "unavailable";
  published_version?: number | null;
  tools: ToolDefinition[];
}

const emptyConnection: ConnectionProfile = {
  transport: "stdio",
  command: [],
  url: "",
  headers: [],
  environment: [],
  installed_project_id: "",
  working_directory: "",
  auto_start: false,
  auto_reconnect: true,
  reconnect_attempts: 2,
  tool_prefix: "",
  network_policy: "public_only",
  timeout_seconds: 30,
  api_base_url: "",
  api_source_url: "",
  api_source_label: "",
  api_spec_version: "",
  api_spec_hash: "",
  api_auth: {
    auth_type: "none",
    credential_id: "",
    api_key_name: "",
    api_key_location: "header",
    username_credential_id: "",
    password_credential_id: "",
    client_id_credential_id: "",
    client_secret_credential_id: "",
    token_url: "",
    scopes: [],
  },
  response_limit_bytes: 2 * 1024 * 1024,
  redirect_limit: 3,
  provider_id: "",
  provider_credential_id: "",
  provider_config: {},
};

const inputClass =
  "w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-300 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-45";
const primaryButton =
  "rounded-md bg-cyan-300 px-3 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-45";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (payload as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : `请求失败：${response.status}`);
  }
  return payload as T;
}

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  const parsed = JSON.parse(value || "{}") as unknown;
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error(`${label}必须是 JSON 对象。`);
  }
  return parsed as Record<string, unknown>;
}

function formatTime(value: number): string {
  return new Date(value * 1000).toLocaleString("zh-CN");
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "connected" || status === "ready" || status === "published"
      ? "bg-emerald-300"
      : status === "error"
        ? "bg-rose-300"
        : "bg-slate-500";
  return <span aria-hidden="true" className={`h-2 w-2 rounded-full ${color}`} />;
}

function InlineMessage({
  tone,
  children,
  onClose,
}: {
  tone: "error" | "success";
  children: string;
  onClose: () => void;
}) {
  const colors =
    tone === "error" ? "bg-rose-500/10 text-rose-100" : "bg-emerald-400/10 text-emerald-100";
  return (
    <div className={`mt-4 flex items-start justify-between gap-3 rounded-md px-3 py-2 text-sm ${colors}`}>
      <span>{children}</span>
      <button className="font-semibold" onClick={onClose} type="button">
        关闭
      </button>
    </div>
  );
}

export default function ToolsetsPage() {
  const [toolsets, setToolsets] = useState<ToolsetDefinition[]>([]);
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [installedProjects, setInstalledProjects] = useState<InstalledMCPProject[]>([]);
  const [providers, setProviders] = useState<BuiltinProvider[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<ToolsetDefinition | null>(null);
  const [commandText, setCommandText] = useState("[]");
  const [newKind, setNewKind] = useState<ToolsetKind>("mcp");
  const [kindFilter, setKindFilter] = useState<
    "all" | "mcp" | "api" | "provider"
  >(() => {
    const tab = new URLSearchParams(window.location.search).get("tab");
    return tab === "mcp" || tab === "api" || tab === "provider" ? tab : "all";
  });
  const [selectedProviderId, setSelectedProviderId] = useState("tavily");
  const [providerCredentialId, setProviderCredentialId] = useState("");
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [importMode, setImportMode] = useState<"text" | "url">("text");
  const [apiDocument, setApiDocument] = useState("");
  const [apiSourceUrl, setApiSourceUrl] = useState("");
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [confirmMutatingTest, setConfirmMutatingTest] = useState(false);
  const [credentialName, setCredentialName] = useState("");
  const [credentialKind, setCredentialKind] = useState<CredentialKind>("header");
  const [credentialValue, setCredentialValue] = useState("");
  const [releaseNotes, setReleaseNotes] = useState("");
  const [activeTool, setActiveTool] = useState("");
  const [toolArguments, setToolArguments] = useState("{}");
  const [toolResult, setToolResult] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const selected = useMemo(
    () => toolsets.find((item) => item.id === selectedId) ?? null,
    [selectedId, toolsets],
  );
  const selectedProvider = useMemo(
    () => providers.find((provider) => provider.id === selectedProviderId) ?? null,
    [providers, selectedProviderId],
  );
  const activeToolDefinition = useMemo(
    () => draft?.tools.find((tool) => tool.original_name === activeTool) ?? null,
    [activeTool, draft],
  );
  const filteredToolsets = useMemo(
    () =>
      toolsets.filter((item) =>
        kindFilter === "all"
          ? true
          : kindFilter === "mcp"
            ? item.kind === "mcp"
            : kindFilter === "provider"
              ? item.kind === "builtin"
              : item.kind === "openapi" || item.kind === "odata",
      ),
    [kindFilter, toolsets],
  );
  const counts = useMemo(
    () => ({
      connected: toolsets.filter((item) =>
        ["connected", "ready"].includes(item.runtime_status),
      ).length,
      published: toolsets.filter((item) => item.status === "published").length,
      enabledTools: toolsets.reduce(
        (sum, item) => sum + item.tools.filter((tool) => tool.enabled).length,
        0,
      ),
    }),
    [toolsets],
  );

  const loadAll = useCallback(async (preferredId?: string) => {
    setLoading(true);
    try {
      const [toolsetPayload, credentialPayload, installedPayload, providerPayload] = await Promise.all([
        requestJson<{ toolsets: ToolsetDefinition[] }>("/api/toolsets"),
        requestJson<{ credentials: CredentialSummary[] }>("/api/runtime/credentials"),
        requestJson<{ installed: InstalledMCPProject[] }>("/api/mcp/installed"),
        requestJson<{ providers: BuiltinProvider[] }>("/api/toolsets/providers").catch(
          () => ({ providers: [] }),
        ),
      ]);
      setToolsets(toolsetPayload.toolsets);
      setCredentials(credentialPayload.credentials);
      setInstalledProjects(installedPayload.installed);
      setProviders(providerPayload.providers);
      setProviderCredentialId((current) =>
        credentialPayload.credentials.some((item) => item.credential_id === current)
          ? current
          : credentialPayload.credentials.find(
              (item) => item.kind === "provider_key" && item.status === "active",
            )?.credential_id || "",
      );
      setSelectedId((current) => {
        const wanted = preferredId || current;
        return toolsetPayload.toolsets.some((item) => item.id === wanted)
          ? wanted
          : toolsetPayload.toolsets[0]?.id || "";
      });
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Toolset 加载失败。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (!selected) {
      setDraft(null);
      return;
    }
    setDraft(structuredClone(selected));
    setCommandText(JSON.stringify(selected.connection.command, null, 2));
    setApiBaseUrl(selected.connection.api_base_url || "");
    setApiSourceUrl(selected.connection.api_source_url || "");
    setApiDocument("");
    setConfirmMutatingTest(false);
    setActiveTool((current) =>
      selected.tools.some((tool) => tool.original_name === current)
        ? current
        : selected.tools[0]?.original_name || "",
    );
    setToolResult("");
  }, [selected]);

  function patchDraft(patch: Partial<ToolsetDefinition>) {
    setDraft((current) => (current ? { ...current, ...patch } : current));
  }

  function patchConnection(patch: Partial<ConnectionProfile>) {
    setDraft((current) =>
      current ? { ...current, connection: { ...current.connection, ...patch } } : current,
    );
  }

  async function createToolset(event: FormEvent) {
    event.preventDefault();
    if (kindFilter !== "provider" && !newName.trim()) return;
    setBusy("create");
    try {
      if (kindFilter === "provider") {
        const created = await requestJson<ToolsetDefinition>(
          `/api/toolsets/providers/${encodeURIComponent(selectedProviderId)}/instances`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: newName.trim() || selectedProvider?.title || selectedProviderId,
              description:
                newDescription.trim() || selectedProvider?.description || "",
              credential_id: providerCredentialId,
              tags: ["builtin-provider", "default-provider"],
            }),
          },
        );
        setNewName("");
        setNewDescription("");
        setNotice("Provider Toolset 已创建并发现工具。");
        await loadAll(created.id);
        return;
      }
      const created = await requestJson<ToolsetDefinition>("/api/toolsets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: newKind,
          name: newName.trim(),
          description: newDescription.trim(),
          connection: emptyConnection,
        }),
      });
      setNewName("");
      setNewDescription("");
      setNotice("Toolset 草稿已创建。");
      await loadAll(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败。");
    } finally {
      setBusy("");
    }
  }

  async function saveDraft(): Promise<ToolsetDefinition | null> {
    if (!draft) return null;
    setBusy("save");
    try {
      let command: string[] = [];
      if (draft.kind === "mcp" && draft.connection.transport === "stdio") {
        const parsed = JSON.parse(commandText) as unknown;
        if (!Array.isArray(parsed) || !parsed.every((item) => typeof item === "string")) {
          throw new Error("Stdio 命令必须是字符串数组。");
        }
        command = parsed;
      }
      const updated = await requestJson<ToolsetDefinition>(`/api/toolsets/${draft.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: draft.revision,
          patch: {
            name: draft.name,
            description: draft.description,
            tags: draft.tags,
            privacy_policy: draft.privacy_policy,
            disclaimer: draft.disclaimer,
            connection: { ...draft.connection, command },
          },
        }),
      });
      setNotice("Toolset 草稿已保存。");
      await loadAll(updated.id);
      return updated;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败。");
      return null;
    } finally {
      setBusy("");
    }
  }

  async function importApiSpec() {
    if (!draft || draft.kind === "mcp") return;
    setBusy("import");
    try {
      const saved = await saveDraft();
      if (!saved) return;
      const imported = await requestJson<ToolsetDefinition>(
        `/api/toolsets/${draft.id}/import`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source_type: importMode,
            document: importMode === "text" ? apiDocument : "",
            source_url: importMode === "url" ? apiSourceUrl.trim() : "",
            base_url: apiBaseUrl.trim(),
            source_label: importMode === "text" ? "管理页导入" : "",
          }),
        },
      );
      setNotice(
        `${draft.kind === "openapi" ? "OpenAPI" : "OData"} 已编译为 ${imported.tools.length} 个操作，新操作默认关闭。`,
      );
      await loadAll(imported.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "API 文档导入失败。");
    } finally {
      setBusy("");
    }
  }

  async function refreshApiSpec() {
    if (!draft || draft.kind === "mcp") return;
    setBusy("refresh");
    try {
      const refreshed = await requestJson<ToolsetDefinition>(
        `/api/toolsets/${draft.id}/refresh`,
        { method: "POST" },
      );
      setNotice("远程 API 文档已刷新，已发布版本保持不变。");
      await loadAll(refreshed.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "API 文档刷新失败。");
    } finally {
      setBusy("");
    }
  }

  async function runConnection(action: "connect" | "disconnect") {
    if (!draft) return;
    setBusy(action);
    try {
      let revision = draft.revision;
      if (action === "connect") {
        const saved = await saveDraft();
        if (!saved) return;
        revision = saved.revision;
      }
      const current = await requestJson<ToolsetDefinition>(
        `/api/toolsets/${draft.id}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision }),
        },
      );
      setNotice(
        action === "connect"
          ? "连接成功，工具 Schema 已刷新。新发现工具默认关闭。"
          : "连接已断开。",
      );
      await loadAll(current.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "连接操作失败。");
    } finally {
      setBusy("");
    }
  }

  async function createCredential(event: FormEvent) {
    event.preventDefault();
    if (!credentialName.trim() || !credentialValue) return;
    setBusy("credential");
    try {
      await requestJson("/api/runtime/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: credentialName.trim(),
          kind: credentialKind,
          value: credentialValue,
        }),
      });
      setCredentialName("");
      setCredentialValue("");
      setNotice("凭据已加密保存，后续只显示掩码。");
      await loadAll(draft?.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "凭据保存失败。");
    } finally {
      setBusy("");
    }
  }

  function updateBinding(
    field: "headers" | "environment",
    index: number,
    patch: Partial<SecretBinding>,
  ) {
    if (!draft) return;
    const next = [...draft.connection[field]];
    next[index] = { ...next[index], ...patch };
    patchConnection({ [field]: next });
  }

  function addBinding(field: "headers" | "environment") {
    if (!draft) return;
    patchConnection({
      [field]: [
        ...draft.connection[field],
        {
          name: "",
          credential_id:
            credentials.find((item) => item.status === "active")?.credential_id || "",
        },
      ],
    });
  }

  function removeBinding(field: "headers" | "environment", index: number) {
    if (!draft) return;
    patchConnection({
      [field]: draft.connection[field].filter((_, position) => position !== index),
    });
  }

  async function updateTool(patch: Partial<ToolDefinition>) {
    if (!draft || !activeToolDefinition) return;
    setBusy("tool");
    try {
      const updated = await requestJson<ToolsetDefinition>(
        `/api/toolsets/${draft.id}/tools/${encodeURIComponent(activeToolDefinition.original_name)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision: draft.revision, patch }),
        },
      );
      setNotice("工具配置已保存。");
      await loadAll(updated.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "工具配置保存失败。");
    } finally {
      setBusy("");
    }
  }

  async function testTool() {
    if (!draft || !activeToolDefinition) return;
    setBusy("test");
    try {
      const result = await requestJson<{
        output: string;
        is_error: boolean;
        metadata: Record<string, unknown>;
      }>(
        `/api/toolsets/${draft.id}/tools/${encodeURIComponent(activeToolDefinition.original_name)}/test`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            arguments: parseJsonObject(toolArguments, "测试参数"),
            confirm_mutating: confirmMutatingTest,
          }),
        },
      );
      setToolResult(result.output || JSON.stringify(result.metadata, null, 2));
      setNotice(result.is_error ? "工具返回了错误结果。" : "工具测试完成。");
    } catch (reason) {
      setToolResult("");
      setError(reason instanceof Error ? reason.message : "工具测试失败。");
    } finally {
      setBusy("");
    }
  }

  async function publishToolset() {
    if (!draft) return;
    setBusy("publish");
    try {
      const version = await requestJson<ToolsetVersion>(`/api/toolsets/${draft.id}/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: draft.revision,
          release_notes: releaseNotes.trim(),
        }),
      });
      setReleaseNotes("");
      setNotice(`Toolset v${version.version} 已发布。`);
      await loadAll(draft.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发布失败。");
    } finally {
      setBusy("");
    }
  }

  return (
    <PageContainer activeResource="mcps" hideSidebar maxWidthClassName="max-w-[1720px]">
      <div className="px-1 py-2 sm:px-2">
        <header className="flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold text-cyan-200">
              <span className="h-2 w-2 rounded-full bg-cyan-300" />
              Runtime Toolset
            </div>
            <h1 className="mt-2 text-2xl font-semibold text-white">Toolset Runtime</h1>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">
              连接 MCP 或导入 OpenAPI/OData，配置工具 Schema 与凭据，再发布不可变版本供 Agent 绑定。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-white/5 px-3 py-1.5 text-xs text-slate-300">
              {toolsets.length} 个 Toolset
            </span>
            <span className="rounded-full bg-emerald-300/10 px-3 py-1.5 text-xs text-emerald-200">
              {counts.connected} 个运行就绪
            </span>
            <span className="rounded-full bg-cyan-300/10 px-3 py-1.5 text-xs text-cyan-100">
              {counts.published} 个已发布 · {counts.enabledTools} 个工具
            </span>
            <Link className={secondaryButton} to="/mcps">
              发现 MCP 项目
            </Link>
            <button className={secondaryButton} onClick={() => void loadAll()} type="button">
              刷新
            </button>
          </div>
        </header>

        <nav aria-label="Toolset 类型" className="mt-4 flex gap-1 border-b border-white/10">
          {([
            ["all", "全部"],
            ["mcp", "MCP"],
            ["api", "API Toolset"],
            ["provider", "内置 Provider"],
          ] as const).map(([value, label]) => (
            <button
              className={`px-3 py-2 text-sm font-semibold ${
                kindFilter === value
                  ? "border-b-2 border-cyan-300 text-cyan-100"
                  : "text-slate-400 hover:text-white"
              }`}
              key={value}
              onClick={() => setKindFilter(value)}
              type="button"
            >
              {label}
            </button>
          ))}
        </nav>

        {error ? <InlineMessage onClose={() => setError("")} tone="error">{error}</InlineMessage> : null}
        {notice ? <InlineMessage onClose={() => setNotice("")} tone="success">{notice}</InlineMessage> : null}

        <div className="mt-5 grid min-h-[720px] gap-5 xl:grid-cols-[260px_minmax(420px,0.9fr)_minmax(420px,1.1fr)]">
          <aside className="border-r border-white/10 pr-5">
            <form className="border-b border-white/10 pb-5" onSubmit={createToolset}>
              <h2 className="text-sm font-semibold text-white">创建 Toolset</h2>
              {kindFilter === "provider" ? (
                <div className="mt-3 space-y-3">
                  <label className="block text-xs font-semibold text-slate-300">
                    Provider
                    <select
                      className={`${inputClass} mt-1`}
                      onChange={(event) => setSelectedProviderId(event.target.value)}
                      value={selectedProviderId}
                    >
                      {providers
                        .filter((provider) => provider.instance_creatable)
                        .map((provider) => (
                          <option
                            className="bg-slate-950"
                            key={provider.id}
                            value={provider.id}
                          >
                            {provider.title}
                          </option>
                        ))}
                    </select>
                  </label>
                  {selectedProvider?.credential_required ? (
                    <>
                      <label className="block text-xs font-semibold text-slate-300">
                        加密 Provider 凭据
                        <select
                          className={`${inputClass} mt-1`}
                          onChange={(event) => setProviderCredentialId(event.target.value)}
                          value={providerCredentialId}
                        >
                          <option className="bg-slate-950" value="">
                            请选择 Provider Key
                          </option>
                          {credentials
                            .filter(
                              (credential) =>
                                credential.kind === "provider_key" &&
                                credential.status === "active",
                            )
                            .map((credential) => (
                              <option
                                className="bg-slate-950"
                                key={credential.credential_id}
                                value={credential.credential_id}
                              >
                                {credential.name} · {credential.masked_value}
                              </option>
                            ))}
                        </select>
                      </label>
                      <p className="text-[11px] leading-5 text-slate-500">
                        Provider Key 只以 Credential ID 固定到版本，页面不会回显明文。
                      </p>
                    </>
                  ) : (
                    <p className="rounded-md border border-emerald-300/15 bg-emerald-300/[0.05] px-3 py-2 text-[11px] leading-5 text-emerald-100">
                      此 Provider 复用当前 Runtime 的持久化作用域，不需要额外凭据。
                    </p>
                  )}
                  <div className="rounded-md border border-white/10 bg-white/[0.035] px-3 py-2 text-[11px] leading-5 text-slate-300">
                    {selectedProvider?.default_toolset_id
                      ? `默认 Toolset · ${
                          selectedProvider.configuration_status === "ready"
                            ? "已就绪"
                            : "待配置"
                        }`
                      : "正在初始化默认 Provider Toolset"}
                  </div>
                </div>
              ) : (
                <div className="mt-3 grid grid-cols-3 gap-1 rounded-md bg-white/5 p-1">
                  {([
                    ["mcp", "MCP"],
                    ["openapi", "OpenAPI"],
                    ["odata", "OData"],
                  ] as Array<[ToolsetKind, string]>).map(([value, label]) => (
                    <button
                      className={`rounded px-2 py-2 text-xs font-semibold ${
                        newKind === value
                          ? "bg-cyan-300 text-slate-950"
                          : "text-slate-400 hover:bg-white/5 hover:text-white"
                      }`}
                      key={value}
                      onClick={() => setNewKind(value)}
                      type="button"
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              <label className="mt-3 block text-xs font-semibold text-slate-300">
                名称
                <input
                  className={`${inputClass} mt-1`}
                  maxLength={160}
                  onChange={(event) => setNewName(event.target.value)}
                  placeholder={
                    kindFilter === "provider"
                      ? selectedProvider?.title || "默认 Provider"
                      : "研究工具集"
                  }
                  value={newName}
                />
              </label>
              <label className="mt-3 block text-xs font-semibold text-slate-300">
                说明
                <textarea
                  className={`${inputClass} mt-1 min-h-20 resize-y`}
                  maxLength={2000}
                  onChange={(event) => setNewDescription(event.target.value)}
                  placeholder="工具用途和使用边界"
                  value={newDescription}
                />
              </label>
              <button
                className={`${primaryButton} mt-3 w-full`}
                disabled={
                  busy === "create" ||
                  (kindFilter !== "provider" && !newName.trim()) ||
                  (kindFilter === "provider" &&
                    Boolean(selectedProvider?.credential_required) &&
                    !providerCredentialId)
                }
                type="submit"
              >
                {busy === "create"
                  ? "配置中..."
                  : kindFilter === "provider"
                    ? "配置并打开默认 Toolset"
                    : "创建草稿"}
              </button>
            </form>

            <div className="pt-4">
              <div className="mb-2 flex items-center justify-between text-xs text-slate-500">
                <span>Toolset</span>
                <span>{filteredToolsets.length}</span>
              </div>
              <div className="divide-y divide-white/10 border-y border-white/10">
                {loading ? <p className="py-5 text-center text-xs text-slate-500">正在加载...</p> : null}
                {!loading && !filteredToolsets.length ? (
                  <p className="py-7 text-center text-xs leading-5 text-slate-500">
                    当前分类还没有 Toolset。
                  </p>
                ) : null}
                {filteredToolsets.map((item) => (
                  <button
                    className={`w-full px-2 py-3 text-left transition ${
                      item.id === selectedId ? "bg-cyan-300/10" : "hover:bg-white/[0.035]"
                    }`}
                    key={item.id}
                    onClick={() => setSelectedId(item.id)}
                    type="button"
                  >
                    <span className="flex items-center gap-2 text-sm font-semibold text-white">
                      <StatusDot status={item.runtime_status} />
                      <span className="truncate">{item.name}</span>
                    </span>
                    <span className="mt-1 flex justify-between gap-2 text-[11px] text-slate-500">
                      <span>
                        {item.kind === "mcp" ? item.connection.transport : item.kind}
                      </span>
                      <span>{item.published_version ? `v${item.published_version}` : "未发布"}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </aside>

          {!draft ? (
            <section className="col-span-2 flex min-h-[560px] items-center justify-center border border-dashed border-white/10">
              <div className="max-w-sm text-center">
                <h2 className="text-base font-semibold text-white">选择一个 Toolset</h2>
                <p className="mt-2 text-sm leading-6 text-slate-500">
                  连接 MCP 或导入 API 文档后会发现工具；启用至少一个操作即可发布版本。
                </p>
              </div>
            </section>
          ) : (
            <>
              <section className="min-w-0 border-r border-white/10 pr-5">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-white">连接与版本</h2>
                    <p className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                      <StatusDot status={draft.runtime_status} />
                      {draft.runtime_status}
                      {draft.runtime_error ? ` · ${draft.runtime_error}` : ""}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    {draft.kind === "mcp" ? (
                      <>
                        <button
                          className={secondaryButton}
                          disabled={busy !== ""}
                          onClick={() => void runConnection("disconnect")}
                          type="button"
                        >
                          断开
                        </button>
                        <button
                          className={primaryButton}
                          disabled={busy !== ""}
                          onClick={() => void runConnection("connect")}
                          type="button"
                        >
                          {busy === "connect" ? "连接中..." : "保存并连接"}
                        </button>
                      </>
                    ) : draft.kind === "builtin" ? (
                      <button
                        className={primaryButton}
                        disabled={busy !== ""}
                        onClick={() => void runConnection("connect")}
                        type="button"
                      >
                        {busy === "connect" ? "刷新中..." : "保存并刷新工具"}
                      </button>
                    ) : (
                      <>
                        <button
                          className={secondaryButton}
                          disabled={busy !== "" || !draft.connection.api_source_url}
                          onClick={() => void refreshApiSpec()}
                          type="button"
                        >
                          {busy === "refresh" ? "刷新中..." : "刷新源"}
                        </button>
                        <button
                          className={primaryButton}
                          disabled={
                            busy !== "" ||
                            (importMode === "text"
                              ? !apiDocument.trim()
                              : !apiSourceUrl.trim())
                          }
                          onClick={() => void importApiSpec()}
                          type="button"
                        >
                          {busy === "import" ? "编译中..." : "保存并导入"}
                        </button>
                      </>
                    )}
                  </div>
                </div>

                <div className="mt-5 space-y-4">
                  <label className="block text-xs font-semibold text-slate-300">
                    名称
                    <input
                      className={`${inputClass} mt-1`}
                      onChange={(event) => patchDraft({ name: event.target.value })}
                      value={draft.name}
                    />
                  </label>
                  <label className="block text-xs font-semibold text-slate-300">
                    说明
                    <textarea
                      className={`${inputClass} mt-1 min-h-20 resize-y`}
                      onChange={(event) => patchDraft({ description: event.target.value })}
                      value={draft.description}
                    />
                  </label>
                  <label className="block text-xs font-semibold text-slate-300">
                    标签（逗号分隔）
                    <input
                      className={`${inputClass} mt-1`}
                      onChange={(event) =>
                        patchDraft({
                          tags: event.target.value
                            .split(",")
                            .map((item) => item.trim())
                            .filter(Boolean),
                        })
                      }
                      value={draft.tags.join(", ")}
                    />
                  </label>

                  {draft.kind === "mcp" ? (
                    <>
                      <fieldset>
                        <legend className="text-xs font-semibold text-slate-300">传输</legend>
                        <div className="mt-2 grid grid-cols-3 gap-1 rounded-md bg-white/5 p-1">
                          {([
                            ["stdio", "Stdio"],
                            ["streamable_http", "HTTP"],
                            ["legacy_sse", "旧 SSE"],
                          ] as Array<[Transport, string]>).map(([value, label]) => (
                            <button
                              className={`rounded px-2 py-2 text-xs font-semibold transition ${
                                draft.connection.transport === value
                                  ? "bg-cyan-300 text-slate-950"
                                  : "text-slate-400 hover:bg-white/5 hover:text-white"
                              }`}
                              key={value}
                              onClick={() => patchConnection({ transport: value })}
                              type="button"
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </fieldset>
                      {draft.connection.transport === "stdio" ? (
                        <>
                          <label className="block text-xs font-semibold text-slate-300">
                            已安装 MCP 项目（可选）
                            <select
                              className={`${inputClass} mt-1`}
                              onChange={(event) => {
                                const project = installedProjects.find(
                                  (item) => item.project_id === event.target.value,
                                );
                                patchConnection({ installed_project_id: event.target.value });
                                if (project?.server_command?.length) {
                                  setCommandText(JSON.stringify(project.server_command, null, 2));
                                }
                              }}
                              value={draft.connection.installed_project_id}
                            >
                              <option className="bg-slate-950" value="">手动配置启动命令</option>
                              {installedProjects.map((project) => (
                                <option
                                  className="bg-slate-950"
                                  key={project.project_id}
                                  value={project.project_id}
                                >
                                  {project.project_id}
                                  {project.npm_package ? ` · ${project.npm_package}` : ""}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="block text-xs font-semibold text-slate-300">
                            启动命令（argv JSON）
                            <textarea
                              className={`${inputClass} mt-1 min-h-24 resize-y font-mono text-xs`}
                              onChange={(event) => setCommandText(event.target.value)}
                              value={commandText}
                            />
                          </label>
                        </>
                      ) : (
                        <label className="block text-xs font-semibold text-slate-300">
                          MCP URL
                          <input
                            className={`${inputClass} mt-1`}
                            onChange={(event) => patchConnection({ url: event.target.value })}
                            placeholder="https://mcp.example.com/mcp"
                            type="url"
                            value={draft.connection.url}
                          />
                        </label>
                      )}
                      <div className="grid gap-2 sm:grid-cols-2">
                        <label className="flex items-center gap-2 rounded-md bg-white/[0.035] px-3 py-2 text-xs text-slate-300">
                          <input
                            checked={draft.connection.auto_reconnect}
                            onChange={(event) => patchConnection({ auto_reconnect: event.target.checked })}
                            type="checkbox"
                          />
                          自动重连
                        </label>
                        <label className="flex items-center gap-2 rounded-md bg-white/[0.035] px-3 py-2 text-xs text-slate-300">
                          <input
                            checked={draft.connection.auto_start}
                            onChange={(event) => patchConnection({ auto_start: event.target.checked })}
                            type="checkbox"
                          />
                          发布后自动恢复
                        </label>
                      </div>
                      <BindingEditor
                        bindings={draft.connection.headers}
                        credentials={credentials}
                        kind="headers"
                        label="Headers"
                        onAdd={() => addBinding("headers")}
                        onRemove={(index) => removeBinding("headers", index)}
                        onUpdate={(index, patch) => updateBinding("headers", index, patch)}
                      />
                      <BindingEditor
                        bindings={draft.connection.environment}
                        credentials={credentials}
                        kind="environment"
                        label="环境变量"
                        onAdd={() => addBinding("environment")}
                        onRemove={(index) => removeBinding("environment", index)}
                        onUpdate={(index, patch) => updateBinding("environment", index, patch)}
                      />
                    </>
                  ) : draft.kind === "builtin" ? (
                    <div className="space-y-4 border-t border-white/10 pt-4">
                      <div className="rounded-md bg-cyan-300/10 px-3 py-3 text-xs leading-5 text-cyan-50">
                        内置 Provider 通过固定受控端点运行；凭据仅以加密引用保存。
                      </div>
                      <label className="block text-xs font-semibold text-slate-300">
                        Provider
                        <input
                          className={`${inputClass} mt-1`}
                          disabled
                          value={draft.connection.provider_id}
                        />
                      </label>
                      <label className="block text-xs font-semibold text-slate-300">
                        Provider 凭据
                        <select
                          className={`${inputClass} mt-1`}
                          onChange={(event) =>
                            patchConnection({
                              provider_credential_id: event.target.value,
                            })
                          }
                          value={draft.connection.provider_credential_id}
                        >
                          <option className="bg-slate-950" value="">
                            请选择 Provider Key
                          </option>
                          {credentials
                            .filter(
                              (credential) =>
                                credential.kind === "provider_key" &&
                                credential.status === "active",
                            )
                            .map((credential) => (
                              <option
                                className="bg-slate-950"
                                key={credential.credential_id}
                                value={credential.credential_id}
                              >
                                {credential.name} · {credential.masked_value}
                              </option>
                            ))}
                        </select>
                      </label>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="block text-xs font-semibold text-slate-300">
                          调用超时（秒）
                          <input
                            className={`${inputClass} mt-1`}
                            max={120}
                            min={5}
                            onChange={(event) =>
                              patchConnection({
                                timeout_seconds: Number(event.target.value),
                              })
                            }
                            type="number"
                            value={draft.connection.timeout_seconds}
                          />
                        </label>
                        <label className="block text-xs font-semibold text-slate-300">
                          响应上限（MB）
                          <input
                            className={`${inputClass} mt-1`}
                            max={10}
                            min={1}
                            onChange={(event) =>
                              patchConnection({
                                response_limit_bytes:
                                  Number(event.target.value) * 1024 * 1024,
                              })
                            }
                            type="number"
                            value={Math.max(
                              1,
                              Math.round(
                                draft.connection.response_limit_bytes / 1024 / 1024,
                              ),
                            )}
                          />
                        </label>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-4 border-t border-white/10 pt-4">
                      <div className="grid grid-cols-2 gap-1 rounded-md bg-white/5 p-1">
                        {(["text", "url"] as const).map((mode) => (
                          <button
                            className={`rounded px-2 py-2 text-xs font-semibold ${
                              importMode === mode
                                ? "bg-cyan-300 text-slate-950"
                                : "text-slate-400 hover:bg-white/5"
                            }`}
                            key={mode}
                            onClick={() => setImportMode(mode)}
                            type="button"
                          >
                            {mode === "text" ? "文本 / 文件导入" : "受控 URL 导入"}
                          </button>
                        ))}
                      </div>
                      {importMode === "text" ? (
                        <>
                          <label className="block text-xs font-semibold text-slate-300">
                            {draft.kind === "openapi" ? "OpenAPI JSON / YAML" : "OData Metadata XML"}
                            <textarea
                              className={`${inputClass} mt-1 min-h-40 resize-y font-mono text-xs`}
                              onChange={(event) => setApiDocument(event.target.value)}
                              placeholder="粘贴 API 文档，或从本地文件读取"
                              value={apiDocument}
                            />
                          </label>
                          <label className={`${secondaryButton} block cursor-pointer text-center`}>
                            从文件读取
                            <input
                              accept=".json,.yaml,.yml,.xml,.edmx"
                              className="sr-only"
                              onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) void file.text().then(setApiDocument);
                              }}
                              type="file"
                            />
                          </label>
                        </>
                      ) : (
                        <label className="block text-xs font-semibold text-slate-300">
                          文档 URL
                          <input
                            className={`${inputClass} mt-1`}
                            onChange={(event) => setApiSourceUrl(event.target.value)}
                            placeholder="https://api.example.com/openapi.json"
                            type="url"
                            value={apiSourceUrl}
                          />
                        </label>
                      )}
                      <label className="block text-xs font-semibold text-slate-300">
                        {draft.kind === "openapi" ? "基础 URL（可覆盖 servers）" : "OData 服务根 URL"}
                        <input
                          className={`${inputClass} mt-1`}
                          onChange={(event) => {
                            setApiBaseUrl(event.target.value);
                            patchConnection({ api_base_url: event.target.value });
                          }}
                          placeholder="https://api.example.com/v1"
                          type="url"
                          value={apiBaseUrl}
                        />
                      </label>
                      <APIAuthEditor
                        auth={draft.connection.api_auth}
                        credentials={credentials}
                        onChange={(api_auth) => patchConnection({ api_auth })}
                      />
                      <label className="block text-xs font-semibold text-slate-300">
                        网络策略
                        <select
                          className={`${inputClass} mt-1`}
                          onChange={(event) =>
                            patchConnection({
                              network_policy: event.target
                                .value as ConnectionProfile["network_policy"],
                            })
                          }
                          value={draft.connection.network_policy}
                        >
                          <option className="bg-slate-950" value="public_only">
                            仅公网，阻断私网与本机
                          </option>
                          <option className="bg-slate-950" value="trusted_private">
                            受控私网（可信管理面）
                          </option>
                        </select>
                      </label>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="block text-xs font-semibold text-slate-300">
                          响应上限（MB）
                          <input
                            className={`${inputClass} mt-1`}
                            max={10}
                            min={1}
                            onChange={(event) =>
                              patchConnection({
                                response_limit_bytes:
                                  Number(event.target.value) * 1024 * 1024,
                              })
                            }
                            type="number"
                            value={Math.max(
                              1,
                              Math.round(
                                draft.connection.response_limit_bytes / 1024 / 1024,
                              ),
                            )}
                          />
                        </label>
                        <label className="block text-xs font-semibold text-slate-300">
                          最大重定向
                          <input
                            className={`${inputClass} mt-1`}
                            max={5}
                            min={0}
                            onChange={(event) =>
                              patchConnection({
                                redirect_limit: Number(event.target.value),
                              })
                            }
                            type="number"
                            value={draft.connection.redirect_limit}
                          />
                        </label>
                      </div>
                      {draft.connection.api_spec_hash ? (
                        <div className="rounded-md bg-white/[0.035] px-3 py-2 text-xs leading-5 text-slate-400">
                          <p>
                            已导入 {draft.connection.api_spec_version} ·{" "}
                            {draft.connection.api_source_label || "manual"}
                          </p>
                          <p className="font-mono text-[10px] text-slate-500">
                            {draft.connection.api_spec_hash.slice(0, 16)}
                          </p>
                        </div>
                      ) : null}
                      {draft.import_warnings?.length ? (
                        <div className="rounded-md bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
                          {draft.import_warnings.slice(0, 5).map((warning) => (
                            <p key={warning}>{warning}</p>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  )}

                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block text-xs font-semibold text-slate-300">
                      工具名前缀
                      <input
                        className={`${inputClass} mt-1`}
                        onChange={(event) =>
                          patchConnection({ tool_prefix: event.target.value })
                        }
                        placeholder="research"
                        value={draft.connection.tool_prefix}
                      />
                    </label>
                    <label className="block text-xs font-semibold text-slate-300">
                      调用超时（秒）
                      <input
                        className={`${inputClass} mt-1`}
                        max={300}
                        min={5}
                        onChange={(event) =>
                          patchConnection({ timeout_seconds: Number(event.target.value) })
                        }
                        type="number"
                        value={draft.connection.timeout_seconds}
                      />
                    </label>
                  </div>


                  <details className="border-t border-white/10 pt-4">
                    <summary className="cursor-pointer text-xs font-semibold text-slate-300">
                      隐私协议与免责声明
                    </summary>
                    <label className="mt-3 block text-xs font-semibold text-slate-300">
                      隐私协议
                      <textarea
                        className={`${inputClass} mt-1 min-h-20 resize-y`}
                        onChange={(event) =>
                          patchDraft({ privacy_policy: event.target.value })
                        }
                        value={draft.privacy_policy}
                      />
                    </label>
                    <label className="mt-3 block text-xs font-semibold text-slate-300">
                      免责声明
                      <textarea
                        className={`${inputClass} mt-1 min-h-20 resize-y`}
                        onChange={(event) => patchDraft({ disclaimer: event.target.value })}
                        value={draft.disclaimer}
                      />
                    </label>
                  </details>

                  <button
                    className={`${secondaryButton} w-full`}
                    disabled={busy !== ""}
                    onClick={() => void saveDraft()}
                    type="button"
                  >
                    {busy === "save" ? "保存中..." : `保存草稿 r${draft.revision}`}
                  </button>
                </div>

                <div className="mt-7 border-t border-white/10 pt-5">
                  <h3 className="text-sm font-semibold text-white">凭据保险箱</h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    Header 与环境变量只引用加密凭据 ID，Toolset 版本不保存明文。
                  </p>
                  <form className="mt-3 space-y-2" onSubmit={createCredential}>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <input
                        className={inputClass}
                        onChange={(event) => setCredentialName(event.target.value)}
                        placeholder="凭据名称"
                        value={credentialName}
                      />
                      <select
                        className={inputClass}
                        onChange={(event) =>
                          setCredentialKind(event.target.value as CredentialKind)
                        }
                        value={credentialKind}
                      >
                        <option className="bg-slate-950" value="header">Header</option>
                        <option className="bg-slate-950" value="environment">环境变量</option>
                        <option className="bg-slate-950" value="provider_key">Provider Key</option>
                        <option className="bg-slate-950" value="generic">通用秘密</option>
                      </select>
                    </div>
                    <input
                      autoComplete="new-password"
                      className={inputClass}
                      onChange={(event) => setCredentialValue(event.target.value)}
                      placeholder="凭据值，仅本次提交"
                      type="password"
                      value={credentialValue}
                    />
                    <button
                      className={`${secondaryButton} w-full`}
                      disabled={
                        busy === "credential" || !credentialName.trim() || !credentialValue
                      }
                      type="submit"
                    >
                      加密保存凭据
                    </button>
                  </form>
                  <div className="mt-3 divide-y divide-white/10 border-y border-white/10">
                    {credentials.map((item) => (
                      <div
                        className="flex items-center justify-between gap-3 py-2 text-xs"
                        key={item.credential_id}
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-semibold text-slate-200">
                            {item.name}
                          </span>
                          <span className="text-slate-500">
                            {item.kind} · {item.masked_value}
                          </span>
                        </span>
                        <span
                          className={
                            item.status === "active" ? "text-emerald-200" : "text-rose-200"
                          }
                        >
                          {item.status}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </section>

              <section className="min-w-0">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <h2 className="text-base font-semibold text-white">工具配置与测试</h2>
                    <p className="mt-1 text-xs text-slate-500">
                      {draft.tools.length} 个已发现，{draft.tools.filter((tool) => tool.enabled).length} 个已启用
                    </p>
                  </div>
                  <span className="text-xs text-slate-500">
                    {draft.kind === "mcp" ? "Schema 在连接时刷新" : "Schema 在导入时编译"}
                  </span>
                </div>

                <div className="mt-4 grid gap-4 lg:grid-cols-[210px_minmax(0,1fr)]">
                  <div className="divide-y divide-white/10 border-y border-white/10">
                    {!draft.tools.length ? (
                      <p className="px-2 py-10 text-center text-xs leading-5 text-slate-500">
                        {draft.kind === "mcp"
                          ? "保存配置并连接 MCP Server 后，工具列表会显示在这里。"
                          : "导入 OpenAPI 或 OData 文档后，可用操作会显示在这里。"}
                      </p>
                    ) : null}
                    {[...draft.tools]
                      .sort(
                        (left, right) =>
                          left.order - right.order ||
                          left.original_name.localeCompare(right.original_name),
                      )
                      .map((tool) => (
                        <button
                          className={`w-full px-2 py-3 text-left transition ${
                            activeTool === tool.original_name
                              ? "bg-cyan-300/10"
                              : "hover:bg-white/[0.035]"
                          }`}
                          key={tool.original_name}
                          onClick={() => {
                            setActiveTool(tool.original_name);
                            setToolArguments(
                              JSON.stringify(tool.default_arguments || {}, null, 2),
                            );
                            setToolResult("");
                            setConfirmMutatingTest(false);
                          }}
                          type="button"
                        >
                          <span className="flex items-center justify-between gap-2">
                            <span className="truncate text-xs font-semibold text-white">
                              {tool.alias || tool.original_name}
                            </span>
                            <span
                              className={`h-2 w-2 rounded-full ${
                                tool.enabled ? "bg-emerald-300" : "bg-slate-600"
                              }`}
                            />
                          </span>
                          <span className="mt-1 block truncate font-mono text-[10px] text-slate-500">
                            {tool.original_name}
                          </span>
                        </button>
                      ))}
                  </div>

                  {activeToolDefinition ? (
                    <div className="min-w-0 space-y-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="truncate text-sm font-semibold text-white">
                            {activeToolDefinition.alias || activeToolDefinition.original_name}
                          </h3>
                          <p className="mt-1 truncate font-mono text-[11px] text-slate-500">
                            {activeToolDefinition.original_name}
                          </p>
                          <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] font-semibold">
                              <span className="rounded bg-white/5 px-2 py-1 text-slate-300">
                                {String(activeToolDefinition.execution.method || "API")}
                              </span>
                              <span
                                className={`rounded px-2 py-1 ${
                                  activeToolDefinition.read_only
                                    ? "bg-emerald-300/10 text-emerald-100"
                                    : "bg-amber-300/10 text-amber-100"
                                }`}
                              >
                                {activeToolDefinition.read_only ? "只读" : "变更操作"}
                              </span>
                              {activeToolDefinition.requires_approval ? (
                                <span className="rounded bg-rose-300/10 px-2 py-1 text-rose-100">
                                  强制 HITL
                                </span>
                              ) : null}
                              {activeToolDefinition.terminal ? (
                                <span className="rounded bg-violet-300/10 px-2 py-1 text-violet-100">
                                  Terminal
                                </span>
                              ) : null}
                              <span className="rounded bg-white/5 px-2 py-1 text-slate-300">
                                memory:{activeToolDefinition.memory_mode}
                              </span>
                            </div>
                        </div>
                        <label className="flex items-center gap-2 text-xs font-semibold text-slate-300">
                          <input
                            checked={activeToolDefinition.enabled}
                            disabled={busy !== ""}
                            onChange={(event) =>
                              void updateTool({ enabled: event.target.checked })
                            }
                            type="checkbox"
                          />
                          启用
                        </label>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <label className="flex items-start gap-2 rounded-md bg-white/[0.035] px-3 py-2 text-xs leading-5 text-slate-300">
                          <input
                            checked={activeToolDefinition.read_only}
                            className="mt-1"
                            disabled={busy !== "" || draft.kind !== "mcp"}
                            onChange={(event) =>
                              void updateTool({ read_only: event.target.checked })
                            }
                            type="checkbox"
                          />
                          <span>
                            <strong className="block text-white">只读工具</strong>
                            MCP 需管理员显式确认；API 工具由导入规范推断。
                          </span>
                        </label>
                        <label className="flex items-start gap-2 rounded-md bg-white/[0.035] px-3 py-2 text-xs leading-5 text-slate-300">
                          <input
                            checked={activeToolDefinition.sensitive}
                            className="mt-1"
                            disabled={busy !== ""}
                            onChange={(event) =>
                              void updateTool({ sensitive: event.target.checked })
                            }
                            type="checkbox"
                          />
                          <span>
                            <strong className="block text-white">敏感</strong>
                            调用前强制 HITL，发布和运行时双重校验。
                          </span>
                        </label>
                        <label className="flex items-start gap-2 rounded-md bg-white/[0.035] px-3 py-2 text-xs leading-5 text-slate-300">
                          <input
                            checked={activeToolDefinition.requires_approval}
                            className="mt-1"
                            disabled={busy !== "" || activeToolDefinition.sensitive}
                            onChange={(event) =>
                              void updateTool({
                                requires_approval: event.target.checked,
                              })
                            }
                            type="checkbox"
                          />
                          <span>
                            <strong className="block text-white">要求审批</strong>
                            非敏感工具也可要求每次调用由人工确认。
                          </span>
                        </label>
                        <label className="flex items-start gap-2 rounded-md bg-white/[0.035] px-3 py-2 text-xs leading-5 text-slate-300">
                          <input
                            checked={activeToolDefinition.terminal}
                            className="mt-1"
                            disabled={busy !== ""}
                            onChange={(event) =>
                              void updateTool({ terminal: event.target.checked })
                            }
                            type="checkbox"
                          />
                          <span>
                            <strong className="block text-white">终点工具</strong>
                            成功结果直接成为最终答案，不再调用模型总结。
                          </span>
                        </label>
                        <label className="flex items-start gap-2 rounded-md bg-white/[0.035] px-3 py-2 text-xs leading-5 text-slate-300">
                          <input
                            checked={activeToolDefinition.parallel_safe}
                            className="mt-1"
                            disabled={
                              busy !== "" ||
                              !activeToolDefinition.read_only ||
                              activeToolDefinition.sensitive ||
                              activeToolDefinition.terminal ||
                              activeToolDefinition.requires_approval
                            }
                            onChange={(event) =>
                              void updateTool({
                                parallel_safe: event.target.checked,
                              })
                            }
                            type="checkbox"
                          />
                          <span>
                            <strong className="block text-white">允许并行</strong>
                            仅只读、非敏感、非终点且无需审批的工具可开启。
                          </span>
                        </label>
                        <label className="flex items-start gap-2 rounded-md bg-white/[0.035] px-3 py-2 text-xs leading-5 text-slate-300">
                          <input
                            checked={activeToolDefinition.public_app_allowed}
                            className="mt-1"
                            disabled={
                              busy !== "" ||
                              !activeToolDefinition.read_only ||
                              activeToolDefinition.sensitive ||
                              activeToolDefinition.memory_mode === "conversation"
                            }
                            onChange={(event) =>
                              void updateTool({
                                public_app_allowed: event.target.checked,
                              })
                            }
                            type="checkbox"
                          />
                          <span>
                            <strong className="block text-white">允许公共 App</strong>
                            仍需 App 开启工具并绑定 Tool Policy。
                          </span>
                        </label>
                      </div>
                      <label className="block text-xs font-semibold text-slate-300">
                        Tool Memory
                        <select
                          className={`${inputClass} mt-1`}
                          onChange={(event) =>
                            void updateTool({
                              memory_mode: event.target.value as ToolMemoryMode,
                            })
                          }
                          value={activeToolDefinition.memory_mode}
                        >
                          <option className="bg-slate-950" value="off">
                            off · 不保存
                          </option>
                          <option className="bg-slate-950" value="run">
                            run · 仅本次 Agent 执行
                          </option>
                          <option className="bg-slate-950" value="conversation">
                            conversation · 私有智能体会话
                          </option>
                        </select>
                      </label>
                      <label className="block text-xs font-semibold text-slate-300">
                        Agent 别名
                        <input
                          className={`${inputClass} mt-1`}
                          defaultValue={activeToolDefinition.alias}
                          key={`${activeToolDefinition.original_name}:alias:${draft.revision}`}
                          onBlur={(event) => {
                            if (event.target.value !== activeToolDefinition.alias) {
                              void updateTool({ alias: event.target.value });
                            }
                          }}
                          placeholder={activeToolDefinition.original_name}
                        />
                      </label>
                      <label className="block text-xs font-semibold text-slate-300">
                        描述覆盖
                        <textarea
                          className={`${inputClass} mt-1 min-h-20 resize-y`}
                          defaultValue={activeToolDefinition.description}
                          key={`${activeToolDefinition.original_name}:description:${draft.revision}`}
                          onBlur={(event) => {
                            if (event.target.value !== activeToolDefinition.description) {
                              void updateTool({ description: event.target.value });
                            }
                          }}
                        />
                      </label>
                      <div>
                        <p className="text-xs font-semibold text-slate-300">参数 Schema</p>
                        <pre className="mt-1 max-h-56 overflow-auto rounded-md bg-black/25 p-3 text-[11px] leading-5 text-slate-300">
                          {JSON.stringify(activeToolDefinition.input_schema, null, 2)}
                        </pre>
                      </div>
                      <label className="block text-xs font-semibold text-slate-300">
                        默认参数 JSON
                        <textarea
                          className={`${inputClass} mt-1 min-h-24 resize-y font-mono text-xs`}
                          defaultValue={JSON.stringify(
                            activeToolDefinition.default_arguments || {},
                            null,
                            2,
                          )}
                          key={`${activeToolDefinition.original_name}:defaults:${draft.revision}`}
                          onBlur={(event) => {
                            try {
                              const value = parseJsonObject(event.target.value, "默认参数");
                              if (
                                JSON.stringify(value) !==
                                JSON.stringify(activeToolDefinition.default_arguments)
                              ) {
                                void updateTool({ default_arguments: value });
                              }
                            } catch (reason) {
                              setError(
                                reason instanceof Error ? reason.message : "默认参数无效。",
                              );
                            }
                          }}
                        />
                      </label>
                      <label className="block text-xs font-semibold text-slate-300">
                        测试参数 JSON
                        <textarea
                          className={`${inputClass} mt-1 min-h-24 resize-y font-mono text-xs`}
                          onChange={(event) => setToolArguments(event.target.value)}
                          value={toolArguments}
                        />
                      </label>
                      {!activeToolDefinition.read_only ? (
                        <label className="flex items-start gap-2 rounded-md bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
                          <input
                            checked={confirmMutatingTest}
                            className="mt-1"
                            onChange={(event) =>
                              setConfirmMutatingTest(event.target.checked)
                            }
                            type="checkbox"
                          />
                          我确认此测试可能修改远程数据。Agent 正式调用时仍必须经过 HITL。
                        </label>
                      ) : null}
                      <button
                        className={`${primaryButton} w-full`}
                        disabled={
                          busy !== "" ||
                          !["connected", "ready"].includes(draft.runtime_status) ||
                          (!activeToolDefinition.read_only && !confirmMutatingTest)
                        }
                        onClick={() => void testTool()}
                        type="button"
                      >
                        {busy === "test" ? "测试中..." : "通过 Runtime Policy 测试工具"}
                      </button>
                      {toolResult ? (
                        <div>
                          <p className="text-xs font-semibold text-slate-300">安全结果摘要</p>
                          <pre className="mt-1 max-h-44 overflow-auto rounded-md bg-black/25 p-3 text-[11px] leading-5 text-slate-300">
                            {toolResult}
                          </pre>
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <div className="flex min-h-72 items-center justify-center border border-dashed border-white/10 px-6 text-center text-xs leading-5 text-slate-500">
                      选择一个工具以查看 Schema、设置别名和执行测试。
                    </div>
                  )}
                </div>

                <div className="mt-7 border-t border-white/10 pt-5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                    <label className="min-w-0 flex-1 text-xs font-semibold text-slate-300">
                      发布说明
                      <input
                        className={`${inputClass} mt-1`}
                        onChange={(event) => setReleaseNotes(event.target.value)}
                        placeholder="本版本启用的工具和用途"
                        value={releaseNotes}
                      />
                    </label>
                    <button
                      className={primaryButton}
                      disabled={
                        busy !== "" ||
                        !["connected", "ready"].includes(draft.runtime_status) ||
                        !draft.tools.some((tool) => tool.enabled)
                      }
                      onClick={() => void publishToolset()}
                      type="button"
                    >
                      {busy === "publish" ? "发布中..." : "发布不可变版本"}
                    </button>
                  </div>
                  <div className="mt-4 divide-y divide-white/10 border-y border-white/10">
                    {[...draft.versions].reverse().map((version) => (
                      <div
                        className="grid gap-2 py-3 text-xs sm:grid-cols-[70px_minmax(0,1fr)_150px]"
                        key={version.version}
                      >
                        <span className="font-semibold text-cyan-100">v{version.version}</span>
                        <span className="truncate text-slate-300">
                          {version.release_notes || "无发布说明"} · {version.tools.length} tools
                        </span>
                        <time className="text-slate-500">
                          {formatTime(version.published_at)}
                        </time>
                      </div>
                    ))}
                    {!draft.versions.length ? (
                      <p className="py-5 text-center text-xs text-slate-500">
                        尚未发布版本。
                      </p>
                    ) : null}
                  </div>
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </PageContainer>
  );
}

function APIAuthEditor({
  auth,
  credentials,
  onChange,
}: {
  auth: APIAuthProfile;
  credentials: CredentialSummary[];
  onChange: (auth: APIAuthProfile) => void;
}) {
  const activeCredentials = credentials.filter((item) => item.status === "active");
  const patch = (value: Partial<APIAuthProfile>) => onChange({ ...auth, ...value });

  const credentialSelect = (
    label: string,
    value: string,
    onValue: (credentialId: string) => void,
  ) => (
    <label className="block text-xs font-semibold text-slate-300">
      {label}
      <select
        className={`${inputClass} mt-1`}
        onChange={(event) => onValue(event.target.value)}
        value={value}
      >
        <option className="bg-slate-950" value="">
          选择加密凭据
        </option>
        {activeCredentials.map((credential) => (
          <option
            className="bg-slate-950"
            key={credential.credential_id}
            value={credential.credential_id}
          >
            {credential.name} · {credential.masked_value}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="space-y-3 rounded-md border border-white/10 bg-white/[0.025] p-3">
      <div>
        <p className="text-xs font-semibold text-slate-200">授权方式</p>
        <p className="mt-1 text-[11px] leading-5 text-slate-500">
          这里只保存凭据引用，API key、密码和客户端密钥不会写入 Toolset。
        </p>
      </div>
      <select
        className={inputClass}
        onChange={(event) =>
          patch({ auth_type: event.target.value as APIAuthType })
        }
        value={auth.auth_type}
      >
        <option className="bg-slate-950" value="none">
          无
        </option>
        <option className="bg-slate-950" value="api_key">
          API Key
        </option>
        <option className="bg-slate-950" value="bearer">
          Bearer Token
        </option>
        <option className="bg-slate-950" value="basic">
          Basic
        </option>
        <option className="bg-slate-950" value="oauth2_client_credentials">
          OAuth2 Client Credentials
        </option>
      </select>

      {auth.auth_type === "api_key" ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-xs font-semibold text-slate-300">
              参数名称
              <input
                className={`${inputClass} mt-1`}
                onChange={(event) => patch({ api_key_name: event.target.value })}
                placeholder="X-API-Key"
                value={auth.api_key_name}
              />
            </label>
            <label className="block text-xs font-semibold text-slate-300">
              注入位置
              <select
                className={`${inputClass} mt-1`}
                onChange={(event) =>
                  patch({
                    api_key_location: event.target
                      .value as APIAuthProfile["api_key_location"],
                  })
                }
                value={auth.api_key_location}
              >
                <option className="bg-slate-950" value="header">
                  Header
                </option>
                <option className="bg-slate-950" value="query">
                  Query
                </option>
              </select>
            </label>
          </div>
          {credentialSelect("API Key 凭据", auth.credential_id, (credential_id) =>
            patch({ credential_id }),
          )}
        </>
      ) : null}

      {auth.auth_type === "bearer"
        ? credentialSelect(
            "Bearer Token 凭据",
            auth.credential_id,
            (credential_id) => patch({ credential_id }),
          )
        : null}

      {auth.auth_type === "basic" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {credentialSelect(
            "用户名凭据",
            auth.username_credential_id,
            (username_credential_id) => patch({ username_credential_id }),
          )}
          {credentialSelect(
            "密码凭据",
            auth.password_credential_id,
            (password_credential_id) => patch({ password_credential_id }),
          )}
        </div>
      ) : null}

      {auth.auth_type === "oauth2_client_credentials" ? (
        <>
          <label className="block text-xs font-semibold text-slate-300">
            Token URL
            <input
              className={`${inputClass} mt-1`}
              onChange={(event) => patch({ token_url: event.target.value })}
              placeholder="https://auth.example.com/oauth/token"
              type="url"
              value={auth.token_url}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            {credentialSelect(
              "Client ID 凭据",
              auth.client_id_credential_id,
              (client_id_credential_id) => patch({ client_id_credential_id }),
            )}
            {credentialSelect(
              "Client Secret 凭据",
              auth.client_secret_credential_id,
              (client_secret_credential_id) =>
                patch({ client_secret_credential_id }),
            )}
          </div>
          <label className="block text-xs font-semibold text-slate-300">
            Scopes
            <input
              className={`${inputClass} mt-1`}
              onChange={(event) =>
                patch({
                  scopes: event.target.value
                    .split(/[\s,]+/)
                    .map((item) => item.trim())
                    .filter(Boolean),
                })
              }
              placeholder="read write"
              value={auth.scopes.join(" ")}
            />
          </label>
        </>
      ) : null}
    </div>
  );
}

function BindingEditor({
  bindings,
  credentials,
  kind,
  label,
  onAdd,
  onRemove,
  onUpdate,
}: {
  bindings: SecretBinding[];
  credentials: CredentialSummary[];
  kind: "headers" | "environment";
  label: string;
  onAdd: () => void;
  onRemove: (index: number) => void;
  onUpdate: (index: number, patch: Partial<SecretBinding>) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-slate-300">{label}</p>
        <button
          className="text-xs font-semibold text-cyan-200 hover:text-cyan-100"
          onClick={onAdd}
          type="button"
        >
          添加引用
        </button>
      </div>
      <div className="mt-2 space-y-2">
        {bindings.map((binding, index) => (
          <div
            className="grid grid-cols-[minmax(90px,0.8fr)_minmax(130px,1.2fr)_34px] gap-2"
            key={`${kind}:${index}`}
          >
            <input
              aria-label={`${label} 名称`}
              className={inputClass}
              onChange={(event) => onUpdate(index, { name: event.target.value })}
              placeholder={kind === "headers" ? "Authorization" : "API_KEY"}
              value={binding.name}
            />
            <select
              aria-label={`${label} 凭据`}
              className={inputClass}
              onChange={(event) =>
                onUpdate(index, { credential_id: event.target.value })
              }
              value={binding.credential_id}
            >
              <option className="bg-slate-950" value="">
                选择加密凭据
              </option>
              {credentials
                .filter((item) => item.status === "active")
                .map((credential) => (
                  <option
                    className="bg-slate-950"
                    key={credential.credential_id}
                    value={credential.credential_id}
                  >
                    {credential.name} · {credential.masked_value}
                  </option>
                ))}
            </select>
            <button
              aria-label={`删除 ${label} 引用`}
              className="rounded-md border border-white/10 text-slate-400 hover:bg-rose-500/10 hover:text-rose-200"
              onClick={() => onRemove(index)}
              type="button"
            >
              ×
            </button>
          </div>
        ))}
        {!bindings.length ? (
          <p className="rounded-md bg-white/[0.025] px-3 py-2 text-xs text-slate-500">
            未配置。连接时不会注入任何 {label}。
          </p>
        ) : null}
      </div>
    </div>
  );
}
