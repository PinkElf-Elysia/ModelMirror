import { useEffect, useMemo, useState } from "react";
import type {
  McpCredentialField,
  McpCredentialVerification,
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
  credentialFields: McpCredentialField[];
  settingFields: McpSettingField[];
  initialBindings: Record<string, string>;
  initialSettings: Record<string, string | number | boolean>;
  initiallyConfigured: boolean;
  credentialVerification: McpCredentialVerification;
  disabled?: boolean;
  onConfigured: (configured: boolean) => void;
  onSessionInvalidated: () => void;
}

async function responseError(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? response.statusText;
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

export default function McpCredentialPanel({
  projectId,
  credentialFields,
  settingFields,
  initialBindings,
  initialSettings,
  initiallyConfigured,
  credentialVerification,
  disabled = false,
  onConfigured,
  onSessionInvalidated,
}: McpCredentialPanelProps) {
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [bindings, setBindings] = useState<Record<string, string>>(initialBindings);
  const [settings, setSettings] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      settingFields.map((field) => [
        field.key,
        String(initialSettings[field.key] ?? field.default ?? ""),
      ]),
    ),
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [creatingSlot, setCreatingSlot] = useState<string | null>(null);
  const [credentialName, setCredentialName] = useState("");
  const [credentialValue, setCredentialValue] = useState("");
  const [creating, setCreating] = useState(false);
  const [pendingRevoke, setPendingRevoke] = useState<CredentialSummary | null>(null);
  const [revoking, setRevoking] = useState(false);
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
  }, [initialBindings]);

  useEffect(() => {
    setSettings(
      Object.fromEntries(
        settingFields.map((field) => [
          field.key,
          String(initialSettings[field.key] ?? field.default ?? ""),
        ]),
      ),
    );
  }, [initialSettings, settingFields]);

  const complete = useMemo(
    () =>
      credentialFields.every((field) => !field.required || Boolean(bindings[field.key])) &&
      settingFields.every((field) => !field.required || Boolean(settings[field.key]?.trim())),
    [bindings, credentialFields, settingFields, settings],
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
        body: JSON.stringify({ settings: typedSettings, credential_bindings: bindings }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setMessage("连接配置已保存；首次成功调用后才会标记凭据已验证。");
      onConfigured(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "目录配置保存失败。");
      onConfigured(false);
    } finally {
      setSaving(false);
    }
  }

  const verification = verificationCopy[credentialVerification];

  return (
    <section
      aria-label="MCP 加密凭据配置"
      className="relative mt-4 border-t border-white/10 pt-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-white">加密凭据</h3>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-400">
            在当前 MCP 卡片内独立创建和管理。明文只提交一次，服务端加密保存后仅返回掩码。
          </p>
        </div>
        <span className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${verification.className}`}>
          {verification.label}
        </span>
      </div>

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
                  aria-expanded={isCreating}
                  className="rounded-lg border border-brand-300/30 bg-brand-300/10 px-3 py-2 text-xs font-semibold text-brand-100 transition hover:bg-brand-300/15 disabled:cursor-not-allowed disabled:opacity-45"
                  disabled={disabled || creating}
                  onClick={() => (isCreating ? stopCreating() : startCreating(field))}
                  type="button"
                >
                  {isCreating ? "收起" : "添加加密凭据"}
                </button>
              </div>

              <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                <select
                  className="min-w-0 flex-1 rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50 focus-visible:ring-2 focus-visible:ring-brand-300/30 disabled:opacity-50"
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
                    className="rounded-lg border border-rose-300/25 px-3 py-2 text-xs font-semibold text-rose-100 transition hover:bg-rose-300/10 disabled:cursor-not-allowed disabled:opacity-45"
                    disabled={revoking}
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
                <div className="mt-3 rounded-lg bg-white/[0.04] p-3">
                  <p className="text-xs font-semibold text-white">新增加密凭据</p>
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    该凭据只允许用于 {field.label}，不能被其他 MCP 或 Toolset 选择。
                  </p>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <label className="text-xs font-semibold text-slate-300">
                      凭据名称
                      <input
                        autoComplete="off"
                        className="mt-1 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-brand-300/50"
                        maxLength={160}
                        onChange={(event) => setCredentialName(event.target.value)}
                        value={credentialName}
                      />
                    </label>
                    <label className="text-xs font-semibold text-slate-300">
                      Token / API Key
                      <input
                        autoComplete="new-password"
                        className="mt-1 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-brand-300/50"
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
                      className="rounded-lg bg-brand-300 px-3 py-2 text-xs font-semibold text-ink-950 transition hover:bg-brand-200 disabled:cursor-not-allowed disabled:opacity-45"
                      disabled={creating || !credentialName.trim() || !credentialValue}
                      onClick={() => void createCredential()}
                      type="button"
                    >
                      {creating ? "正在加密保存…" : "加密保存并选择"}
                    </button>
                    <button
                      className="rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.06]"
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
            <h4 className="text-xs font-semibold text-white">服务配置</h4>
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
                        className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-brand-300/50"
                        disabled={disabled}
                        id={fieldId}
                        onChange={(event) => {
                          setSettings((current) => ({ ...current, [field.key]: event.target.value }));
                          setMessage("");
                          onConfigured(false);
                        }}
                        value={settings[field.key] ?? ""}
                      >
                        <option value="">请选择</option>
                        {field.options.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        autoComplete="off"
                        className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50 focus-visible:ring-2 focus-visible:ring-brand-300/30 disabled:opacity-50"
                        disabled={disabled}
                        id={fieldId}
                        max={field.maximum ?? undefined}
                        maxLength={253}
                        min={field.minimum ?? undefined}
                        onChange={(event) => {
                          setSettings((current) => ({ ...current, [field.key]: event.target.value }));
                          setMessage("");
                          onConfigured(false);
                        }}
                        placeholder={field.allowed_hostname_suffixes[0] ? `example${field.allowed_hostname_suffixes[0]}` : undefined}
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
      </div>

      {pendingRevoke ? (
        <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-300/[0.07] p-3">
          <p className="text-xs font-semibold text-rose-100">确认撤销“{pendingRevoke.name}”</p>
          <p className="mt-1 text-xs leading-5 text-slate-300">
            关联会话会立即断开，已保存的连接配置也会失效。此操作不会显示或导出凭据明文。
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              className="rounded-lg bg-rose-300 px-3 py-2 text-xs font-semibold text-ink-950 disabled:opacity-45"
              disabled={revoking}
              onClick={() => void revokeCredential()}
              type="button"
            >
              {revoking ? "正在撤销…" : "确认撤销凭据"}
            </button>
            <button
              className="rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200"
              disabled={revoking}
              onClick={() => setPendingRevoke(null)}
              type="button"
            >
              取消
            </button>
          </div>
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          className="rounded-lg border border-brand-300/35 bg-brand-300/10 px-4 py-2 text-xs font-semibold text-brand-100 transition hover:bg-brand-300/15 disabled:cursor-not-allowed disabled:opacity-45"
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
          {error || message || (!complete ? "请先创建或选择凭据，并完成必填配置。" : "")}
        </span>
      </div>
    </section>
  );
}
