import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  LoaderCircle,
  Plug,
  RefreshCw,
  Server,
} from "lucide-react";
import NewApiChatCertification from "./NewApiChatCertification";

type ConnectionKind =
  | "openrouter"
  | "newapi"
  | "openai_compatible"
  | "openai";
type ConnectionScope =
  | "chat"
  | "audio"
  | "realtime"
  | "embedding"
  | "rerank"
  | "batch";
type ConnectionHealth = "untested" | "online" | "offline" | "disabled";

interface RouterConnection {
  id: string;
  name: string;
  kind: ConnectionKind;
  base_url: string;
  masked_key: string;
  scopes?: ConnectionScope[];
  enabled: boolean;
  health: ConnectionHealth;
  model_count: number;
  last_checked_at?: string | null;
  last_error_hint?: string | null;
}

interface ConnectionTestResult {
  ok: boolean;
  health: ConnectionHealth;
  model_count: number;
  models_preview: string[];
  message: string;
  checked_at: string;
}

interface ConnectionForm {
  kind: ConnectionKind;
  name: string;
  base_url: string;
  api_key: string;
  scopes: ConnectionScope[];
}

const PROVIDERS: Record<
  ConnectionKind,
  {
    label: string;
    name: string;
    baseUrl: string;
    hint: string;
    scopes: ConnectionScope[];
  }
> = {
  openrouter: {
    label: "OpenRouter",
    name: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    hint: "适合直接使用 OpenRouter 的统一模型目录。",
    scopes: ["chat", "audio"],
  },
  newapi: {
    label: "newAPI",
    name: "独立 newAPI",
    baseUrl: "",
    hint: "请输入独立数据面的显式地址；内网地址还需由服务端精确加入 host:port 白名单。",
    scopes: ["chat"],
  },
  openai_compatible: {
    label: "其他 OpenAI 兼容服务",
    name: "自定义模型服务",
    baseUrl: "",
    hint: "适合带有 /v1/models 与 /v1/chat/completions 接口的服务。",
    scopes: ["chat"],
  },
  openai: {
    label: "OpenAI 音频服务",
    name: "OpenAI 音频与实时语音",
    baseUrl: "https://api.openai.com/v1",
    hint: "仅用于音频能力与实时语音，不会自动加入普通聊天或智能调度。",
    scopes: ["audio", "realtime"],
  },
};

const SCOPE_LABELS: Record<ConnectionScope, string> = {
  chat: "普通模型调用",
  audio: "音频能力",
  realtime: "实时语音",
  embedding: "Embedding",
  rerank: "Rerank",
  batch: "异步 Batch",
};

const ALL_SCOPES = Object.keys(SCOPE_LABELS) as ConnectionScope[];

const INITIAL_FORM: ConnectionForm = {
  kind: "openrouter",
  name: PROVIDERS.openrouter.name,
  base_url: PROVIDERS.openrouter.baseUrl,
  api_key: "",
  scopes: [...PROVIDERS.openrouter.scopes],
};

function healthLabel(health: ConnectionHealth) {
  if (health === "online") return "可用";
  if (health === "offline") return "需检查";
  if (health === "disabled") return "已停用";
  return "未测试";
}

function scopesForConnection(connection: RouterConnection) {
  return connection.scopes?.length
    ? connection.scopes
    : PROVIDERS[connection.kind]?.scopes ?? ["chat"];
}

function healthClass(health: ConnectionHealth) {
  if (health === "online") {
    return "border-emerald-300/25 bg-emerald-300/10 text-emerald-200";
  }
  if (health === "offline") {
    return "border-rose-300/25 bg-rose-300/10 text-rose-200";
  }
  return "border-white/10 bg-white/[0.045] text-slate-300";
}

async function readError(response: Response) {
  if (response.status === 401) return "管理会话已失效，请重新配对。";
  if (response.status === 503) return "Provider 管理面尚未配置或暂时不可用。";
  try {
    const payload = await response.json();
    const detail = payload?.detail;
    if (typeof detail === "string") return detail;
    if (typeof detail?.message === "string") return detail.message;
  } catch {
    // Fall through to the stable, user-facing fallback.
  }
  return "操作未完成，请检查服务状态后重试。";
}

export default function ModelServiceConnections({
  csrfToken,
}: {
  csrfToken: string;
}) {
  const [connections, setConnections] = useState<RouterConnection[]>([]);
  const [form, setForm] = useState<ConnectionForm>(INITIAL_FORM);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const provider = PROVIDERS[form.kind];
  const canTest = Boolean(
    form.name.trim() && form.base_url.trim() && form.api_key.trim(),
  );
  const canSaveEdit = Boolean(
    editingId && form.name.trim() && form.base_url.trim() && form.scopes.length,
  );

  const loadConnections = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/router/connections");
      if (!response.ok) throw new Error(await readError(response));
      setConnections((await response.json()) as RouterConnection[]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取模型服务连接。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConnections();
  }, [loadConnections]);

  const updateForm = useCallback(
    (updates: Partial<ConnectionForm>) => {
      setForm((current) => ({ ...current, ...updates }));
      setTestResult(null);
      setError("");
    },
    [],
  );

  const selectProvider = useCallback(
    (kind: ConnectionKind) => {
      const next = PROVIDERS[kind];
      updateForm({
        kind,
        name: next.name,
        base_url: next.baseUrl,
        scopes: [...next.scopes],
      });
    },
    [updateForm],
  );

  const toggleScope = useCallback((scope: ConnectionScope) => {
    setForm((current) => {
      const selected = current.scopes.includes(scope);
      if (selected && current.scopes.length === 1) return current;
      return {
        ...current,
        scopes: selected
          ? current.scopes.filter((item) => item !== scope)
          : ALL_SCOPES.filter((item) => [...current.scopes, scope].includes(item)),
      };
    });
  }, []);

  const payload = useMemo(
    () => ({
      kind: form.kind,
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key,
      scopes: form.scopes,
      enabled: true,
    }),
    [form],
  );

  const testNewConnection = useCallback(async () => {
    if (!canTest) return;
    setTesting(true);
    setError("");
    setTestResult(null);
    try {
      const response = await fetch("/api/router/connections/test", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-ModelMirror-CSRF": csrfToken,
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await readError(response));
      setTestResult((await response.json()) as ConnectionTestResult);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "连接测试失败。");
    } finally {
      setTesting(false);
    }
  }, [canTest, csrfToken, payload]);

  const saveConnection = useCallback(async () => {
    if (!testResult?.ok) return;
    setSaving(true);
    setError("");
    try {
      const response = await fetch("/api/router/connections", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-ModelMirror-CSRF": csrfToken,
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await readError(response));
      const created = (await response.json()) as RouterConnection;
      const tested = await fetch(
        `/api/router/connections/${encodeURIComponent(created.id)}/test`,
        { method: "POST", headers: { "X-ModelMirror-CSRF": csrfToken } },
      );
      if (!tested.ok) throw new Error(await readError(tested));
      setForm(INITIAL_FORM);
      setTestResult(null);
      await loadConnections();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存连接失败。");
    } finally {
      setSaving(false);
    }
  }, [csrfToken, loadConnections, payload, testResult]);

  const beginEdit = useCallback((connection: RouterConnection) => {
    setEditingId(connection.id);
    setForm({
      kind: connection.kind,
      name: connection.name,
      base_url: connection.base_url,
      api_key: "",
      scopes: scopesForConnection(connection),
    });
    setTestResult(null);
    setError("");
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setForm(INITIAL_FORM);
    setTestResult(null);
    setError("");
  }, []);

  const saveEditedConnection = useCallback(async () => {
    if (!editingId || !canSaveEdit) return;
    setSaving(true);
    setError("");
    try {
      const updatePayload: Record<string, unknown> = {
        name: form.name.trim(),
        base_url: form.base_url.trim(),
        scopes: form.scopes,
      };
      if (form.api_key.trim()) updatePayload.api_key = form.api_key;
      const updated = await fetch(
        `/api/router/connections/${encodeURIComponent(editingId)}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify(updatePayload),
        },
      );
      if (!updated.ok) throw new Error(await readError(updated));
      const tested = await fetch(
        `/api/router/connections/${encodeURIComponent(editingId)}/test`,
        { method: "POST", headers: { "X-ModelMirror-CSRF": csrfToken } },
      );
      if (!tested.ok) throw new Error(await readError(tested));
      setEditingId(null);
      setForm(INITIAL_FORM);
      await loadConnections();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "连接修改未完成。");
      await loadConnections();
    } finally {
      setSaving(false);
    }
  }, [canSaveEdit, csrfToken, editingId, form, loadConnections]);

  const testSavedConnection = useCallback(
    async (connectionId: string) => {
      setBusyId(connectionId);
      setError("");
      try {
        const response = await fetch(
          `/api/router/connections/${encodeURIComponent(connectionId)}/test`,
          { method: "POST", headers: { "X-ModelMirror-CSRF": csrfToken } },
        );
        if (!response.ok) throw new Error(await readError(response));
        await loadConnections();
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "连接测试失败。");
        await loadConnections();
      } finally {
        setBusyId(null);
      }
    },
    [csrfToken, loadConnections],
  );

  const toggleConnection = useCallback(
    async (connection: RouterConnection) => {
      setBusyId(connection.id);
      setError("");
      try {
        const response = await fetch(
          `/api/router/connections/${encodeURIComponent(connection.id)}`,
          {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              "X-ModelMirror-CSRF": csrfToken,
            },
            body: JSON.stringify({ enabled: !connection.enabled }),
          },
        );
        if (!response.ok) throw new Error(await readError(response));
        await loadConnections();
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "更新连接失败。");
      } finally {
        setBusyId(null);
      }
    },
    [csrfToken, loadConnections],
  );

  return (
    <section className="mb-6 overflow-hidden rounded-lg border border-white/10 bg-ink-950/82 shadow-prism">
      <div className="flex flex-col gap-3 border-b border-white/10 bg-white/[0.035] px-5 py-5 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-hire-100">
            <Server className="h-4 w-4" aria-hidden="true" />
            <p className="text-sm font-semibold">模型服务连接</p>
          </div>
          <h2 className="mt-2 text-2xl font-semibold text-white">
            连接可调用的模型服务
          </h2>
          <p className="mt-2 max-w-[70ch] text-sm leading-6 text-slate-300">
            选择服务、填写地址和密钥，然后先测试再保存。密钥只会加密保存在本机后端。
          </p>
        </div>
        <div className="text-sm text-slate-300">
          已保存 <span className="font-semibold text-white">{connections.length}</span>{" "}
          个连接
        </div>
      </div>

      <ol className="grid border-b border-white/10 bg-slate-950/45 sm:grid-cols-3">
        {[
          ["1", "选择服务"],
          ["2", "填写连接信息"],
          ["3", "测试并保存"],
        ].map(([number, label], index) => (
          <li
            className={`flex items-center gap-3 px-5 py-3 text-sm ${
              index < 2 ? "border-b border-white/10 sm:border-b-0 sm:border-r" : ""
            }`}
            key={number}
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-hire-300/15 text-xs font-semibold text-hire-100">
              {number}
            </span>
            <span className="font-medium text-slate-200">{label}</span>
          </li>
        ))}
      </ol>

      <div className="grid gap-0 lg:grid-cols-[minmax(0,1.05fr)_minmax(340px,0.95fr)]">
        <form
          className="space-y-5 border-b border-white/10 p-5 lg:border-b-0 lg:border-r"
          onSubmit={(event) => {
            event.preventDefault();
            if (editingId) void saveEditedConnection();
            else void testNewConnection();
          }}
        >
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-200">
              模型服务
            </span>
            <select
              className="w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none transition focus:border-cyan-300/60"
              disabled={editingId !== null}
              onChange={(event) =>
                selectProvider(event.target.value as ConnectionKind)
              }
              value={form.kind}
            >
              {Object.entries(PROVIDERS).map(([kind, item]) => (
                <option key={kind} value={kind}>
                  {item.label}
                </option>
              ))}
            </select>
            <span className="mt-2 block text-xs leading-5 text-slate-400">
              {provider.hint}
            </span>
            <fieldset className="mt-3">
              <legend className="text-xs font-medium text-slate-300">
                授权用途（旧连接不会自动扩大）
              </legend>
              <span className="mt-2 flex flex-wrap gap-2">
                {ALL_SCOPES.map((scope) => {
                  const checked = form.scopes.includes(scope);
                  return (
                    <label
                      className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs transition ${checked ? "border-cyan-300/35 bg-cyan-300/10 text-cyan-100" : "border-white/10 text-slate-400"}`}
                      key={scope}
                    >
                      <input
                        checked={checked}
                        className="sr-only"
                        disabled={checked && form.scopes.length === 1}
                        onChange={() => toggleScope(scope)}
                        type="checkbox"
                      />
                      {SCOPE_LABELS[scope]}
                    </label>
                  );
                })}
              </span>
            </fieldset>
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-200">
                连接名称
              </span>
              <input
                className="w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-cyan-300/60"
                maxLength={120}
                onChange={(event) => updateForm({ name: event.target.value })}
                placeholder="例如：团队 OpenRouter"
                value={form.name}
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-200">
                API 密钥
              </span>
              <input
                autoComplete="new-password"
                className="w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-cyan-300/60"
                onChange={(event) => updateForm({ api_key: event.target.value })}
                placeholder={editingId ? "留空以保留现有密钥" : "仅发送到本机后端"}
                type="password"
                value={form.api_key}
              />
            </label>
          </div>

          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-200">
              服务地址
            </span>
            <input
              className="w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2.5 font-mono text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-cyan-300/60"
              maxLength={2048}
              onChange={(event) => updateForm({ base_url: event.target.value })}
              placeholder="https://example.com/v1"
              spellCheck={false}
              type="url"
              value={form.base_url}
            />
          </label>

          {error ? (
            <div
              className="flex gap-3 rounded-lg bg-rose-400/10 px-3 py-3 text-sm text-rose-100"
              role="alert"
            >
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span>{error}</span>
            </div>
          ) : null}

          {testResult ? (
            <div
              className={`rounded-lg px-4 py-3 ${
                testResult.ok
                  ? "bg-emerald-300/10 text-emerald-100"
                  : "bg-amber-300/10 text-amber-100"
              }`}
              role="status"
            >
              <div className="flex items-start gap-2 text-sm font-medium">
                {testResult.ok ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                ) : (
                  <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                )}
                <span>{testResult.message}</span>
              </div>
              {testResult.models_preview.length > 0 ? (
                <p className="mt-2 truncate font-mono text-xs opacity-80">
                  {testResult.models_preview.join(" · ")}
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="flex flex-wrap gap-3">
            <button
              className="inline-flex items-center justify-center gap-2 rounded-full border border-cyan-300/30 bg-cyan-300/10 px-4 py-2 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/18 disabled:cursor-not-allowed disabled:opacity-45"
              disabled={editingId ? !canSaveEdit || saving : !canTest || testing || saving}
              type="submit"
            >
              {testing ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Plug className="h-4 w-4" />
              )}
              {editingId ? "保存修改并测试" : "测试连接"}
            </button>
            {editingId ? (
              <button className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200" disabled={saving} onClick={cancelEdit} type="button">取消编辑</button>
            ) : <button
              className="rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-45"
              disabled={!testResult?.ok || saving || testing}
              onClick={() => void saveConnection()}
              type="button"
            >
              {saving ? "正在保存" : "保存连接"}
            </button>}
          </div>
        </form>

        <div className="min-w-0 p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-white">已保存连接</h3>
              <p className="mt-1 text-xs text-slate-400">
                可随时测试、停用或恢复，不会删除配置。
              </p>
            </div>
            <button
              aria-label="刷新连接列表"
              className="rounded-full p-2 text-slate-300 transition hover:bg-white/[0.07] hover:text-white"
              onClick={() => void loadConnections()}
              type="button"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>

          {loading && connections.length === 0 ? (
            <div className="space-y-3" aria-label="正在读取连接">
              <div className="h-20 animate-pulse rounded-lg bg-white/[0.045]" />
              <div className="h-20 animate-pulse rounded-lg bg-white/[0.045]" />
            </div>
          ) : connections.length === 0 ? (
            <div className="rounded-lg bg-white/[0.035] px-4 py-8 text-center">
              <Server className="mx-auto h-6 w-6 text-slate-400" />
              <p className="mt-3 text-sm font-medium text-slate-200">
                还没有模型服务连接
              </p>
              <p className="mt-1 text-xs leading-5 text-slate-400">
                完成左侧三步后，连接只会进入对应的模型与音频功能。
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-white/10">
              {connections.map((connection) => (
                <li className="py-4 first:pt-0 last:pb-0" key={connection.id}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-medium text-white">{connection.name}</p>
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs ${healthClass(
                            connection.health,
                          )}`}
                        >
                          {healthLabel(connection.health)}
                        </span>
                      </div>
                      <p className="mt-1 truncate font-mono text-xs text-slate-400">
                        {connection.base_url}
                      </p>
                      <p className="mt-2 text-xs text-slate-400">
                        {connection.masked_key} · {connection.model_count} 个模型
                      </p>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {scopesForConnection(connection).map((scope) => (
                          <span
                            className="rounded-full bg-white/[0.055] px-2 py-0.5 text-[11px] text-slate-300"
                            key={scope}
                          >
                            {SCOPE_LABELS[scope]}
                          </span>
                        ))}
                      </div>
                      {connection.last_error_hint ? (
                        <p className="mt-2 text-xs leading-5 text-amber-200">
                          {connection.last_error_hint}
                        </p>
                      ) : null}
                      {scopesForConnection(connection).includes("chat") ? (
                        <NewApiChatCertification
                          connectionEnabled={connection.enabled}
                          connectionId={connection.id}
                          connectionKind={connection.kind}
                          csrfToken={csrfToken}
                        />
                      ) : null}
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <button
                        className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-white/[0.07] disabled:opacity-45"
                        disabled={busyId === connection.id || editingId === connection.id}
                        onClick={() => beginEdit(connection)}
                        type="button"
                      >
                        编辑
                      </button>
                      <button
                        className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-white/[0.07] disabled:opacity-45"
                        disabled={!connection.enabled || busyId === connection.id}
                        onClick={() => void testSavedConnection(connection.id)}
                        type="button"
                      >
                        测试
                      </button>
                      <button
                        className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-white/[0.07] disabled:opacity-45"
                        disabled={busyId === connection.id}
                        onClick={() => void toggleConnection(connection)}
                        type="button"
                      >
                        {connection.enabled ? "停用" : "恢复"}
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
