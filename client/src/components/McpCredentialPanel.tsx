import { useEffect, useMemo, useState } from "react";
import type {
  McpCredentialField,
  McpCredentialVerification,
  McpDatabasePreflightStatus,
  McpSaasPolicy,
  McpSaasAccountStatus,
  McpSettingField,
} from "../data/mcpAdaptationPlan";

interface CredentialSummary {
  credential_id: string;
  name: string;
  kind: string;
  masked_value: string;
  status: "active" | "unavailable" | "revoked";
  catalog_project_id: string;
  catalog_slot: string;
}

interface McpCredentialPanelProps {
  projectId: string;
  mode?: "service" | "database" | "saas";
  credentialFields: McpCredentialField[];
  settingFields: McpSettingField[];
  initialBindings: Record<string, string>;
  initialSettings: Record<string, string | number | boolean>;
  workspaceId?: string | null;
  initiallyConfigured: boolean;
  credentialVerification: McpCredentialVerification;
  databasePreflightStatus?: McpDatabasePreflightStatus;
  saasPolicy?: McpSaasPolicy | null;
  accountStatus?: McpSaasAccountStatus;
  disabled?: boolean;
  connectionPending?: boolean;
  onConfigured: (configured: boolean) => void;
  onConfigurationSaved?: (
    settings: Record<string, string | number | boolean>,
    bindings: Record<string, string>,
  ) => void;
  onSessionInvalidated: () => void;
}

interface UnbindResponse {
  ok: true;
  project_id: string;
  disconnected: boolean;
  revoked_credentials: number;
}

async function responseError(response: Response) {
  try {
    const payload = (await response.json()) as {
      detail?: string | { message?: string };
      error?: string;
    };
    if (typeof payload.detail === "string") return payload.detail;
    if (payload.detail?.message) return payload.detail.message;
    return payload.error ?? response.statusText;
  } catch {
    return response.statusText;
  }
}

const verificationCopy: Record<
  McpCredentialVerification,
  { label: string; className: string }
> = {
  "not-required": {
    label: "此适配器不需要凭据",
    className: "border-slate-300/20 bg-slate-300/[0.06] text-slate-300",
  },
  missing: {
    label: "尚未配置加密凭据",
    className: "border-slate-300/20 bg-slate-300/[0.06] text-slate-300",
  },
  unverified: {
    label: "凭据尚未验证，连接只代表传输已建立",
    className: "border-amber-300/25 bg-amber-300/[0.08] text-amber-100",
  },
  verified: {
    label: "凭据已通过代表性调用验证",
    className: "border-emerald-300/25 bg-emerald-300/[0.08] text-emerald-100",
  },
  "verification-failed": {
    label: "最近一次调用未通过，请检查凭据或参数",
    className: "border-rose-300/25 bg-rose-300/[0.08] text-rose-100",
  },
};

const databasePreflightVerifiedCopy = {
  label: "当前连接已通过自动数据源预检",
  className: "border-emerald-300/25 bg-emerald-300/[0.08] text-emerald-100",
};

const saasPreflightCopy: Record<
  McpDatabasePreflightStatus,
  { label: string; className: string }
> = {
  "not-applicable": {
    label: "等待账号配置",
    className: "border-slate-300/20 bg-slate-300/[0.06] text-slate-300",
  },
  blocked: {
    label: "账号连接已阻断",
    className: "border-rose-300/25 bg-rose-300/[0.08] text-rose-100",
  },
  "awaiting-workspace": {
    label: "等待账号范围",
    className: "border-amber-300/25 bg-amber-300/[0.08] text-amber-100",
  },
  "awaiting-configuration": {
    label: "等待保存账号与资源范围",
    className: "border-amber-300/25 bg-amber-300/[0.08] text-amber-100",
  },
  unverified: {
    label: "配置已保存，等待连接预检",
    className: "border-amber-300/25 bg-amber-300/[0.08] text-amber-100",
  },
  verifying: {
    label: "正在验证账号与资源范围",
    className: "border-cyan-300/25 bg-cyan-300/[0.08] text-cyan-100",
  },
  verified: {
    label: "账号、权限与资源范围预检通过",
    className: "border-emerald-300/25 bg-emerald-300/[0.08] text-emerald-100",
  },
  failed: {
    label: "账号预检失败，请检查凭据与资源 ID",
    className: "border-rose-300/25 bg-rose-300/[0.08] text-rose-100",
  },
};

const saasAccountCopy: Record<
  McpSaasAccountStatus,
  { label: string; className: string }
> = {
  "not-applicable": {
    label: "账号状态不适用",
    className: "border-slate-300/20 bg-slate-300/[0.06] text-slate-300",
  },
  blocked: {
    label: "账号绑定已阻断",
    className: "border-rose-300/25 bg-rose-300/[0.08] text-rose-100",
  },
  unbound: {
    label: "账号尚未绑定",
    className: "border-slate-300/20 bg-slate-300/[0.06] text-slate-300",
  },
  unverified: {
    label: "账号已配置，尚未验证",
    className: "border-amber-300/25 bg-amber-300/[0.08] text-amber-100",
  },
  verified: {
    label: "账号绑定已验证",
    className: "border-emerald-300/25 bg-emerald-300/[0.08] text-emerald-100",
  },
};

function visibleSettingOptions(projectId: string, field: McpSettingField) {
  if (projectId !== "dbhub" || field.key !== "engine") return field.options;
  return field.options.filter(
    (option) => !["sqlserver", "mssql"].includes(option.value.toLowerCase()),
  );
}

function initialSettingValue(
  projectId: string,
  field: McpSettingField,
  initialSettings: Record<string, string | number | boolean>,
) {
  const value = String(initialSettings[field.key] ?? field.default ?? "");
  if (field.kind !== "enum") return value;
  const options = visibleSettingOptions(projectId, field);
  return options.some((option) => option.value === value)
    ? value
    : (options[0]?.value ?? "");
}

function dbhubDefaultPort(engine: string) {
  const normalized = engine.trim().toLowerCase();
  if (normalized === "postgres" || normalized === "postgresql") return "5432";
  if (normalized === "mysql" || normalized === "mariadb") return "3306";
  return "";
}

function writeRetryModeLabel(mode: string) {
  const labels: Record<string, string> = {
    never: "不自动重试",
    "idempotency-key-only": "仅复用幂等结果，不重新发出写入",
  };
  return labels[mode] ?? "服务端受控且不自动重试";
}

function hasCustomizedDbhubPort(
  projectId: string,
  settingFields: McpSettingField[],
  initialSettings: Record<string, string | number | boolean>,
) {
  if (projectId !== "dbhub") return false;
  const engineField = settingFields.find((field) => field.key === "engine");
  const portField = settingFields.find((field) => field.key === "port");
  if (!engineField || !portField) return false;
  const engine = initialSettingValue(projectId, engineField, initialSettings);
  const port = initialSettingValue(projectId, portField, initialSettings).trim();
  const defaultPort = dbhubDefaultPort(engine);
  return Boolean(port && defaultPort && port !== defaultPort);
}

export default function McpCredentialPanel({
  projectId,
  mode = "service",
  credentialFields,
  settingFields,
  initialBindings,
  initialSettings,
  workspaceId = null,
  initiallyConfigured,
  credentialVerification,
  databasePreflightStatus = "not-applicable",
  saasPolicy = null,
  accountStatus = "not-applicable",
  disabled = false,
  connectionPending = false,
  onConfigured,
  onConfigurationSaved,
  onSessionInvalidated,
}: McpCredentialPanelProps) {
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [bindings, setBindings] = useState<Record<string, string>>(initialBindings);
  const [settings, setSettings] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      settingFields.map((field) => [
        field.key,
        initialSettingValue(projectId, field, initialSettings),
      ]),
    ),
  );
  const [dbhubPortCustomized, setDbhubPortCustomized] = useState(() =>
    hasCustomizedDbhubPort(projectId, settingFields, initialSettings),
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [creatingSlot, setCreatingSlot] = useState<string | null>(null);
  const [credentialName, setCredentialName] = useState("");
  const [credentialValue, setCredentialValue] = useState("");
  const [creating, setCreating] = useState(false);
  const [pendingRevoke, setPendingRevoke] = useState<CredentialSummary | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [tenantConfirmed, setTenantConfirmed] = useState(
    mode !== "saas" || initiallyConfigured,
  );
  const [accountBound, setAccountBound] = useState(initiallyConfigured);
  const [showUnbindConfirmation, setShowUnbindConfirmation] = useState(false);
  const [revokeOnUnbind, setRevokeOnUnbind] = useState(false);
  const [unbinding, setUnbinding] = useState(false);
  const [message, setMessage] = useState(
    initiallyConfigured ? "配置已保存，可建立受控连接。" : "",
  );
  const [error, setError] = useState("");

  async function loadCredentials() {
    setLoading(true);
    try {
      const response = await fetch(`/api/mcp/catalog/${projectId}/credentials`);
      if (!response.ok) throw new Error(await responseError(response));
      const payload = (await response.json()) as { credentials: CredentialSummary[] };
      setCredentials(payload.credentials.filter((item) => item.status === "active"));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取目录加密凭据。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadCredentials();
  }, [projectId]);

  useEffect(() => {
    setBindings(initialBindings);
    setAccountBound(initiallyConfigured);
    if (mode === "saas" && initiallyConfigured) setTenantConfirmed(true);
  }, [initialBindings, initiallyConfigured, mode]);

  useEffect(() => {
    setSettings(
      Object.fromEntries(
        settingFields.map((field) => [
          field.key,
          initialSettingValue(projectId, field, initialSettings),
        ]),
      ),
    );
    setDbhubPortCustomized(
      hasCustomizedDbhubPort(projectId, settingFields, initialSettings),
    );
  }, [initialSettings, projectId, settingFields]);

  const complete = useMemo(
    () =>
      credentialFields.every((field) => !field.required || Boolean(bindings[field.key])) &&
      settingFields.every((field) => !field.required || Boolean(settings[field.key]?.trim())) &&
      (mode !== "saas" || tenantConfirmed),
    [bindings, credentialFields, mode, settingFields, settings, tenantConfirmed],
  );

  function startCreating(field: McpCredentialField) {
    setCreatingSlot(field.key);
    setCredentialName(field.label);
    setCredentialValue("");
    setError("");
    setMessage("");
  }

  function stopCreating() {
    setCreatingSlot(null);
    setCredentialName("");
    setCredentialValue("");
  }

  async function createCredential() {
    if (!creatingSlot || !credentialName.trim() || !credentialValue || creating || disabled) {
      return;
    }
    setCreating(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`/api/mcp/catalog/${projectId}/credentials`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slot: creatingSlot,
          name: credentialName.trim(),
          value: credentialValue,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const created = (await response.json()) as CredentialSummary;
      setCredentials((current) => [created, ...current]);
      setBindings((current) => ({ ...current, [creatingSlot]: created.credential_id }));
      setMessage("凭据已加密保存并选中，请继续保存连接配置。");
      onConfigured(false);
      stopCreating();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加密凭据保存失败。");
    } finally {
      setCreating(false);
    }
  }

  async function revokeCredential() {
    if (!pendingRevoke || revoking) return;
    setRevoking(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(
        `/api/mcp/catalog/${projectId}/credentials/${pendingRevoke.credential_id}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(await responseError(response));
      setCredentials((current) =>
        current.filter((item) => item.credential_id !== pendingRevoke.credential_id),
      );
      setBindings((current) =>
        Object.fromEntries(
          Object.entries(current).filter(([, value]) => value !== pendingRevoke.credential_id),
        ),
      );
      setPendingRevoke(null);
      setMessage("凭据已撤销；关联会话已断开，请创建或选择新的凭据。");
      onConfigured(false);
      onSessionInvalidated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "撤销凭据失败。");
    } finally {
      setRevoking(false);
    }
  }

  async function unbindAccount() {
    if (mode !== "saas" || unbinding || !saasPolicy?.account_unbind_supported) return;
    setUnbinding(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`/api/mcp/catalog/${projectId}/unbind`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revoke_credentials: revokeOnUnbind }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as UnbindResponse;
      setBindings({});
      setSettings(
        Object.fromEntries(
          settingFields.map((field) => [
            field.key,
            String(field.default ?? ""),
          ]),
        ),
      );
      setTenantConfirmed(false);
      setAccountBound(false);
      setShowUnbindConfirmation(false);
      setRevokeOnUnbind(false);
      if (result.revoked_credentials > 0) await loadCredentials();
      setMessage(
        result.revoked_credentials > 0
          ? `账号已解绑，并撤销 ${result.revoked_credentials} 条模镜加密凭据。上游 Token 仍需在服务商后台单独撤销。`
          : "账号已解绑；会话、审批和资源配置已清除，加密凭据仍保留在模镜中。",
      );
      onConfigured(false);
      onConfigurationSaved?.({}, {});
      onSessionInvalidated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "账号解绑失败。");
    } finally {
      setUnbinding(false);
    }
  }

  async function save() {
    if (!complete || saving || disabled) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const typedSettings = Object.fromEntries(
        settingFields
          .filter((field) => settings[field.key]?.trim())
          .map((field) => [
            field.key,
            field.kind === "integer" ? Number(settings[field.key]) : settings[field.key].trim(),
          ]),
      );
      const response = await fetch(`/api/mcp/catalog/${projectId}/configuration`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          settings: typedSettings,
          credential_bindings: bindings,
          workspace_id: workspaceId,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setMessage(
        mode === "database"
          ? "数据库配置已保存；连接时会自动校验目标并执行代表性只读预检。"
          : mode === "saas"
            ? "账号与资源范围已保存；连接时会验证凭据、最小权限和目标资源。"
          : "连接配置已保存；首次成功调用后才会标记凭据已验证。",
      );
      onConfigurationSaved?.(typedSettings, bindings);
      if (mode === "saas") setAccountBound(true);
      onConfigured(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "目录配置保存失败。");
      onConfigured(false);
    } finally {
      setSaving(false);
    }
  }

  const isDatabase = mode === "database";
  const isSaas = mode === "saas";
  const isSupabase = projectId === "supabase-mcp";
  const verification = isSaas
    ? saasPreflightCopy[databasePreflightStatus]
    : isDatabase &&
    databasePreflightStatus === "verified" &&
    credentialVerification !== "missing" &&
    credentialVerification !== "not-required" &&
    credentialVerification !== "verification-failed"
      ? databasePreflightVerifiedCopy
      : verificationCopy[credentialVerification];

  return (
    <section
      aria-label={
        isSaas
          ? "MCP SaaS 账号与加密凭据配置"
          : isDatabase
            ? "MCP 数据库连接配置"
            : "MCP 加密凭据配置"
      }
      className="relative mt-4 border-t border-white/10 pt-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-white">
            {isSaas
              ? "账号范围与加密凭据"
              : isDatabase
                ? "数据库连接与加密凭据"
                : "加密凭据"}
          </h3>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-400">
            {isSaas
              ? "凭据与资源 ID 只在当前卡片管理。页面不接收任意 URL、Header、命令或环境变量，也不跳转外站登录；明文只提交一次。"
              : isDatabase
              ? isSupabase
                ? "20 位小写英文字母 project_ref（不含数字）与 PAT 只在当前卡片管理，不使用远程 OAuth；凭据明文只提交一次。"
                : "连接字段与凭据只在当前卡片管理。请分别填写受控字段，不要粘贴 DSN、URI 或宿主路径；凭据明文只提交一次。"
              : "在当前 MCP 卡片内独立创建和管理。明文只提交一次，服务端加密保存后仅返回掩码。"}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {isSaas ? (
            <span className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${saasAccountCopy[accountStatus].className}`}>
              {saasAccountCopy[accountStatus].label}
            </span>
          ) : null}
          <span className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${verification.className}`}>
            {verification.label}
          </span>
        </div>
      </div>

      {isSaas && saasPolicy ? (
        <div className="mt-3 rounded-lg border border-cyan-300/15 bg-cyan-300/[0.05] p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-semibold text-cyan-100">受控账号边界</p>
            <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 font-semibold text-cyan-50">
              {saasPolicy.provider}
            </span>
          </div>
          <dl className="mt-3 grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg bg-black/15 p-2.5">
              <dt className="text-slate-400">固定上游主机</dt>
              <dd className="mt-1 break-words font-semibold text-slate-100">
                {saasPolicy.fixed_hosts.join("、") || "由服务端固定"}
              </dd>
            </div>
            <div className="rounded-lg bg-black/15 p-2.5">
              <dt className="text-slate-400">调用护栏</dt>
              <dd className="mt-1 font-semibold text-slate-100">
                每分钟 {saasPolicy.rate_limit_per_minute} 次 · 最多并发 {saasPolicy.max_concurrent_calls}
              </dd>
            </div>
          </dl>
          <p className="mt-2 leading-5 text-slate-400">
            只读调用最多重试 {saasPolicy.read_retry_limit} 次；写入策略为“{writeRetryModeLabel(saasPolicy.write_retry_mode)}”，限流或结果未知时不会自动重试。
          </p>
        </div>
      ) : null}

      <div className="mt-3 space-y-4">
        {credentialFields.map((field) => {
          const fieldId = `${projectId}-${field.key}-credential`;
          const options = credentials.filter(
            (item) => item.catalog_slot === field.key && item.status === "active",
          );
          const selected = options.find(
            (item) => item.credential_id === bindings[field.key],
          );
          const isCreating = creatingSlot === field.key;
          return (
            <fieldset className="border-y border-white/10 py-3" key={field.key}>
              <legend className="sr-only">{field.label}</legend>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <label className="min-w-0 flex-1" htmlFor={fieldId}>
                  <span className="text-xs font-semibold text-slate-200">
                    {field.label}{field.required ? " · 必填" : ""}
                  </span>
                  <span className="mt-1 block text-xs leading-5 text-slate-500">
                    {field.description}
                  </span>
                </label>
                <button
                  aria-controls={`${fieldId}-create`}
                  aria-expanded={isCreating}
                  className="min-h-11 rounded-lg border border-brand-300/30 bg-brand-300/10 px-3 py-2 text-xs font-semibold text-brand-100 transition hover:bg-brand-300/15 disabled:cursor-not-allowed disabled:opacity-45"
                  disabled={disabled || creating}
                  onClick={() => (isCreating ? stopCreating() : startCreating(field))}
                  type="button"
                >
                  {isCreating ? "收起" : "添加加密凭据"}
                </button>
              </div>

              <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                <select
                  className="min-h-11 min-w-0 flex-1 rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50 focus-visible:ring-2 focus-visible:ring-brand-300/30 disabled:opacity-50"
                  disabled={disabled || loading}
                  id={fieldId}
                  onChange={(event) => {
                    setBindings((current) => ({ ...current, [field.key]: event.target.value }));
                    setMessage("");
                    onConfigured(false);
                  }}
                  value={bindings[field.key] ?? ""}
                >
                  <option value="">{loading ? "正在读取凭据…" : "选择此 MCP 的加密凭据"}</option>
                  {options.map((item) => (
                    <option key={item.credential_id} value={item.credential_id}>
                      {item.name} · {item.masked_value}
                    </option>
                  ))}
                </select>
                {selected ? (
                  <button
                    className="min-h-11 rounded-lg border border-rose-300/25 px-3 py-2 text-xs font-semibold text-rose-100 transition hover:bg-rose-300/10 disabled:cursor-not-allowed disabled:opacity-45"
                    disabled={connectionPending || revoking}
                    onClick={() => setPendingRevoke(selected)}
                    type="button"
                  >
                    撤销所选凭据
                  </button>
                ) : null}
              </div>

              {!loading && options.length === 0 && !isCreating ? (
                <p className="mt-2 text-xs leading-5 text-amber-200">
                  当前 MCP 尚无凭据，请点击“添加加密凭据”。
                </p>
              ) : null}

              {isCreating ? (
                <div className="mt-3 rounded-lg bg-white/[0.04] p-3" id={`${fieldId}-create`}>
                  <p className="text-xs font-semibold text-white">新增加密凭据</p>
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    该凭据只允许用于 {field.label}，不能被其他 MCP 或 Toolset 选择。
                  </p>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <label className="text-xs font-semibold text-slate-300">
                      凭据名称
                      <input
                        autoComplete="off"
                        className="mt-1 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-brand-300/50"
                        maxLength={160}
                        onChange={(event) => setCredentialName(event.target.value)}
                        value={credentialName}
                      />
                    </label>
                    <label className="text-xs font-semibold text-slate-300">
                      {field.label}（明文仅本次提交）
                      <input
                        autoComplete="new-password"
                        className="mt-1 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-brand-300/50"
                        maxLength={20_000}
                        onChange={(event) => setCredentialValue(event.target.value)}
                        placeholder="仅本次提交，保存后不可查看"
                        type="password"
                        value={credentialValue}
                      />
                    </label>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      className="min-h-11 rounded-lg bg-brand-300 px-3 py-2 text-xs font-semibold text-ink-950 transition hover:bg-brand-200 disabled:cursor-not-allowed disabled:opacity-45"
                      disabled={creating || !credentialName.trim() || !credentialValue}
                      onClick={() => void createCredential()}
                      type="button"
                    >
                      {creating ? "正在加密保存…" : "加密保存并选择"}
                    </button>
                    <button
                      className="min-h-11 rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.06]"
                      onClick={stopCreating}
                      type="button"
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : null}
            </fieldset>
          );
        })}

        {settingFields.length > 0 ? (
          <div>
            <h4 className="text-xs font-semibold text-white">
              {isSaas ? "账号与资源范围" : isDatabase ? "数据库连接字段" : "服务配置"}
            </h4>
            {isSaas ? (
              <p className="mt-1 text-xs leading-5 text-slate-400">
                仅填写服务端声明的账号或资源 ID。当前没有资源发现接口，因此这里不会虚构资源选择器，也不会接受资源 URL。
              </p>
            ) : isDatabase ? (
              <p className="mt-1 text-xs leading-5 text-slate-400">
                {isSupabase
                  ? "project_ref 仅接受 20 位小写英文字母，不含数字；页面不会接收 API URL、OAuth 回调或完整连接串。"
                  : "主机、端口、库名、TLS 和用户名按字段独立校验；页面不会生成或展示完整连接串。"}
              </p>
            ) : null}
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              {settingFields.map((field) => {
                const fieldId = `${projectId}-${field.key}-setting`;
                return (
                  <label className="block" htmlFor={fieldId} key={field.key}>
                    <span className="text-xs font-semibold text-slate-200">
                      {field.label}{field.required ? " · 必填" : ""}
                    </span>
                    <span className="mt-1 block text-xs leading-5 text-slate-500">
                      {field.description}
                    </span>
                    {field.kind === "enum" ? (
                      <select
                        className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-brand-300/50"
                        disabled={disabled}
                        id={fieldId}
                        onChange={(event) => {
                          const nextValue = event.target.value;
                          setSettings((current) => {
                            const next = { ...current, [field.key]: nextValue };
                            if (
                              projectId === "dbhub" &&
                              field.key === "engine" &&
                              !dbhubPortCustomized
                            ) {
                              const defaultPort = dbhubDefaultPort(nextValue);
                              if (defaultPort) next.port = defaultPort;
                            }
                            return next;
                          });
                          setMessage("");
                          onConfigured(false);
                        }}
                        value={settings[field.key] ?? ""}
                      >
                        <option value="">请选择</option>
                        {visibleSettingOptions(projectId, field).map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        autoComplete="off"
                        className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50 focus-visible:ring-2 focus-visible:ring-brand-300/30 disabled:opacity-50"
                        disabled={disabled}
                        id={fieldId}
                        max={field.maximum ?? undefined}
                        maxLength={253}
                        min={field.minimum ?? undefined}
                        onChange={(event) => {
                          if (projectId === "dbhub" && field.key === "port") {
                            setDbhubPortCustomized(true);
                          }
                          setSettings((current) => ({ ...current, [field.key]: event.target.value }));
                          setMessage("");
                          onConfigured(false);
                        }}
                        placeholder={
                          field.key === "project_ref"
                            ? "abcdefghijklmnopqrst"
                            : field.allowed_hostname_suffixes[0]
                            ? `example${field.allowed_hostname_suffixes[0]}`
                            : field.kind === "hostname"
                              ? "db.example.com"
                              : undefined
                        }
                        spellCheck={false}
                        type={field.kind === "integer" ? "number" : "text"}
                        value={settings[field.key] ?? ""}
                      />
                    )}
                  </label>
                );
              })}
            </div>
          </div>
        ) : null}

        {isSaas ? (
          <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-3">
            <input
              checked={tenantConfirmed}
              className="mt-0.5 h-5 w-5 shrink-0 accent-cyan-300"
              disabled={disabled}
              onChange={(event) => {
                setTenantConfirmed(event.target.checked);
                setMessage("");
                onConfigured(false);
              }}
              type="checkbox"
            />
            <span>
              <span className="block text-xs font-semibold text-amber-100">
                我确认这是固定 tenant/owner 的单租户实例
              </span>
              <span className="mt-1 block text-xs leading-5 text-slate-400">
                多用户或多租户共享部署尚未开放；切换账号或资源范围前应先解绑当前账号。
              </span>
            </span>
          </label>
        ) : null}
      </div>

      {pendingRevoke ? (
        <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-300/[0.07] p-3">
          <p className="text-xs font-semibold text-rose-100">确认撤销“{pendingRevoke.name}”</p>
          <p className="mt-1 text-xs leading-5 text-slate-300">
            关联会话会立即断开，已保存的连接配置也会失效。此操作不会显示或导出凭据明文。
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              className="min-h-11 rounded-lg bg-rose-300 px-3 py-2 text-xs font-semibold text-ink-950 disabled:opacity-45"
              disabled={connectionPending || revoking}
              onClick={() => void revokeCredential()}
              type="button"
            >
              {revoking ? "正在撤销…" : "确认撤销凭据"}
            </button>
            <button
              className="min-h-11 rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200"
              disabled={revoking}
              onClick={() => setPendingRevoke(null)}
              type="button"
            >
              取消
            </button>
          </div>
        </div>
      ) : null}

      {isSaas && saasPolicy?.account_unbind_supported && accountBound ? (
        <div className="mt-4 border-t border-white/10 pt-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-2xl">
              <p className="text-xs font-semibold text-white">账号解绑</p>
              <p className="mt-1 text-xs leading-5 text-slate-400">
                解绑会断开会话、作废审批并清除账号与资源配置。默认保留模镜内的加密凭据，便于之后重新绑定。
              </p>
            </div>
            <button
              aria-controls={`${projectId}-unbind-confirmation`}
              aria-expanded={showUnbindConfirmation}
              className="min-h-11 rounded-lg border border-rose-300/25 px-3 py-2 text-xs font-semibold text-rose-100 transition hover:bg-rose-300/10 disabled:opacity-45"
              disabled={connectionPending || unbinding}
              onClick={() => setShowUnbindConfirmation((current) => !current)}
              type="button"
            >
              {showUnbindConfirmation ? "收起解绑确认" : "解绑当前账号"}
            </button>
          </div>

          {showUnbindConfirmation ? (
            <div
              className="mt-3 rounded-lg border border-rose-300/20 bg-rose-300/[0.07] p-3"
              id={`${projectId}-unbind-confirmation`}
            >
              <p className="text-xs font-semibold text-rose-100">再次确认解绑账号</p>
              <p className="mt-1 text-xs leading-5 text-slate-300">
                当前账号快照与所有未完成写入审批都会失效。已发出的上游操作不会被撤回。
              </p>
              <label className="mt-3 flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-white/10 bg-black/15 p-3">
                <input
                  checked={revokeOnUnbind}
                  className="mt-0.5 h-5 w-5 shrink-0 accent-rose-300"
                  disabled={connectionPending || unbinding}
                  onChange={(event) => setRevokeOnUnbind(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  <span className="block text-xs font-semibold text-slate-100">
                    同时撤销此账号绑定的模镜加密凭据
                  </span>
                  <span className="mt-1 block text-xs leading-5 text-slate-400">
                    只删除模镜内的加密副本，不等于撤销服务商后台的 Token；如需彻底失效，请同时在上游后台撤销。
                  </span>
                </span>
              </label>
              <div className="mt-3 flex flex-col-reverse gap-2 sm:flex-row">
                <button
                  className="min-h-11 rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200"
                  disabled={connectionPending || unbinding}
                  onClick={() => {
                    setShowUnbindConfirmation(false);
                    setRevokeOnUnbind(false);
                  }}
                  type="button"
                >
                  取消
                </button>
                <button
                  className="min-h-11 rounded-lg bg-rose-300 px-3 py-2 text-xs font-semibold text-ink-950 disabled:opacity-45"
                  disabled={unbinding}
                  onClick={() => void unbindAccount()}
                  type="button"
                >
                  {unbinding ? "正在解绑…" : "确认解绑账号"}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          className="min-h-11 rounded-lg border border-brand-300/35 bg-brand-300/10 px-4 py-2 text-xs font-semibold text-brand-100 transition hover:bg-brand-300/15 disabled:cursor-not-allowed disabled:opacity-45"
          disabled={!complete || saving || disabled}
          onClick={() => void save()}
          type="button"
        >
          {saving ? "正在保存…" : "保存连接配置"}
        </button>
        <span
          aria-live="polite"
          className={`text-xs ${error ? "text-rose-200" : "text-emerald-200"}`}
          role="status"
        >
          {error || message || (!complete
            ? isSaas && !tenantConfirmed
              ? "请完成凭据与必填资源配置，并确认单租户边界。"
              : "请先创建或选择凭据，并完成必填配置。"
            : "")}
        </span>
      </div>
    </section>
  );
}
