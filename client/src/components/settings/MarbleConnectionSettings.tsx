import {
  CheckCircle2,
  CircleAlert,
  Eye,
  EyeOff,
  Globe2,
  KeyRound,
  LoaderCircle,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";

type MarbleSettings = {
  configured: boolean;
  enabled: boolean;
  masked_key: string | null;
  remaining_credits: number | null;
};

const EMPTY_SETTINGS: MarbleSettings = {
  configured: false,
  enabled: false,
  masked_key: null,
  remaining_credits: null,
};

async function readError(response: Response) {
  try {
    const payload = (await response.json()) as {
      detail?: string | { message?: string };
    };
    if (typeof payload.detail === "string") return payload.detail;
    if (typeof payload.detail?.message === "string") return payload.detail.message;
  } catch {
    // Fall through to a stable user-facing message.
  }
  return "操作未完成，请检查服务状态后重试。";
}

export default function MarbleConnectionSettings() {
  const [settings, setSettings] = useState<MarbleSettings>(EMPTY_SETTINGS);
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState(false);
  const [togglingMode, setTogglingMode] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/world-generations/settings/marble", {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return (await response.json()) as MarbleSettings;
      })
      .then(setSettings)
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "无法读取 Marble 设置。");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  async function saveKey(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleanKey = apiKey.trim();
    if (!cleanKey) return;
    setSavingKey(true);
    setError("");
    setNotice("");
    setConfirmClear(false);
    try {
      const response = await fetch("/api/world-generations/settings/marble", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: cleanKey }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const value = (await response.json()) as MarbleSettings;
      setSettings(value);
      setApiKey("");
      setShowKey(false);
      setNotice(
        value.enabled
          ? "Key 已验证并更新，真实模式保持启用。"
          : "Key 已验证并保存，可以开启真实模式。",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Marble Key 保存失败。");
    } finally {
      setSavingKey(false);
    }
  }

  async function toggleMode(nextEnabled: boolean) {
    if (!settings.configured || togglingMode) return;
    setTogglingMode(true);
    setError("");
    setNotice("");
    setConfirmClear(false);
    try {
      const response = await fetch("/api/world-generations/settings/marble", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: nextEnabled }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const value = (await response.json()) as MarbleSettings;
      setSettings(value);
      setNotice(
        value.enabled
          ? "Marble 真实模式已启用，新的世界生成任务将调用真实服务。"
          : "Marble 真实模式已关闭，新的世界生成任务将使用 Mock。",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "运行模式更新失败。");
    } finally {
      setTogglingMode(false);
    }
  }

  async function clearKey() {
    setClearing(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/world-generations/settings/marble", {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(await readError(response));
      setSettings((await response.json()) as MarbleSettings);
      setApiKey("");
      setShowKey(false);
      setConfirmClear(false);
      setNotice("Marble Key 已清除，真实模式已关闭。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Marble Key 清除失败。");
    } finally {
      setClearing(false);
    }
  }

  const busy = savingKey || togglingMode || clearing;

  if (loading) {
    return (
      <section
        aria-label="正在读取 Marble 设置"
        className="mb-6 overflow-hidden rounded-lg border border-white/10 bg-ink-950/82 shadow-prism"
      >
        <div className="space-y-3 border-b border-white/10 px-5 py-5">
          <div className="h-4 w-36 animate-pulse rounded bg-white/[0.07] motion-reduce:animate-none" />
          <div className="h-6 w-60 animate-pulse rounded bg-white/[0.07] motion-reduce:animate-none" />
          <div className="h-4 max-w-xl animate-pulse rounded bg-white/[0.05] motion-reduce:animate-none" />
        </div>
        <div className="grid gap-6 p-5 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="h-32 animate-pulse rounded-lg bg-white/[0.04] motion-reduce:animate-none" />
          <div className="h-32 animate-pulse rounded-lg bg-white/[0.04] motion-reduce:animate-none" />
        </div>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="marble-settings-title"
      className="mb-6 overflow-hidden rounded-lg border border-white/10 bg-ink-950/82 shadow-prism"
    >
      <div className="flex flex-col gap-3 border-b border-white/10 bg-white/[0.035] px-5 py-5 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-hire-100">
            <Globe2 aria-hidden="true" className="h-4 w-4" />
            <p className="text-sm font-semibold">世界生成服务</p>
          </div>
          <h2
            className="mt-2 text-2xl font-semibold text-white"
            id="marble-settings-title"
          >
            World Labs Marble
          </h2>
          <p className="mt-2 max-w-[70ch] text-sm leading-6 text-slate-300">
            配置用于三维世界生成的 Marble Key。验证凭据不会创建任务，开启真实模式后提交生成或导出 PLY 才可能消耗 credits。
          </p>
        </div>
        <div
          aria-live="polite"
          className={`inline-flex w-fit items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold ${
            settings.enabled
              ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
              : settings.configured
                ? "border-white/10 bg-white/[0.045] text-slate-200"
                : "border-white/10 bg-white/[0.035] text-slate-400"
          }`}
        >
          <span
            aria-hidden="true"
            className={`h-1.5 w-1.5 rounded-full ${
              settings.enabled
                ? "bg-emerald-300"
                : settings.configured
                  ? "bg-amber-200"
                  : "bg-slate-500"
            }`}
          />
          {settings.enabled
            ? "Marble 真实模式"
            : settings.configured
              ? "Key 已配置，当前为 Mock"
              : "尚未配置"}
        </div>
      </div>

      <div className="grid lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="border-b border-white/10 p-5 lg:border-b-0 lg:border-r">
          <form onSubmit={(event) => void saveKey(event)}>
            <div className="flex items-center gap-2 text-sm font-semibold text-white">
              <KeyRound aria-hidden="true" className="h-4 w-4 text-cyan-200" />
              配置 API Key
            </div>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              Key 仅发送到本机后端并加密保存，页面不会读取或回显明文。
            </p>
            <label className="mt-4 block" htmlFor="marble-api-key">
              <span className="mb-2 block text-sm font-medium text-slate-200">
                Marble API Key
              </span>
              <span className="flex rounded-lg border border-white/15 bg-slate-950 transition focus-within:border-cyan-300/60 focus-within:ring-4 focus-within:ring-cyan-300/10">
                <input
                  aria-describedby="marble-key-help"
                  autoComplete="new-password"
                  className="min-w-0 flex-1 bg-transparent px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-500 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={busy}
                  id="marble-api-key"
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    setError("");
                    setNotice("");
                  }}
                  placeholder={settings.masked_key || "粘贴 World Labs API Key"}
                  type={showKey ? "text" : "password"}
                  value={apiKey}
                />
                <button
                  aria-label={showKey ? "隐藏 Marble Key" : "显示 Marble Key"}
                  aria-pressed={showKey}
                  className="inline-flex min-w-11 items-center justify-center border-l border-white/10 text-slate-400 transition hover:bg-white/[0.05] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-200 disabled:opacity-50"
                  disabled={busy || !apiKey}
                  onClick={() => setShowKey((current) => !current)}
                  type="button"
                >
                  {showKey ? (
                    <EyeOff aria-hidden="true" size={16} />
                  ) : (
                    <Eye aria-hidden="true" size={16} />
                  )}
                </button>
              </span>
            </label>
            <p className="mt-2 text-xs leading-5 text-slate-500" id="marble-key-help">
              {settings.configured
                ? `当前凭据：${settings.masked_key}`
                : "保存前会调用 Marble credits 接口验证凭据。"}
            </p>

            <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <button
                className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-cyan-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                disabled={busy || !apiKey.trim()}
                type="submit"
              >
                {savingKey ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="animate-spin motion-reduce:animate-none"
                    size={16}
                  />
                ) : (
                  <CheckCircle2 aria-hidden="true" size={16} />
                )}
                {settings.configured ? "验证并更新 Key" : "验证并保存 Key"}
              </button>
              {settings.configured ? (
                <button
                  className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-200/70 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={busy}
                  onClick={() => setConfirmClear(true)}
                  type="button"
                >
                  <Trash2 aria-hidden="true" size={16} />
                  清除 Key
                </button>
              ) : null}
            </div>
          </form>

          {confirmClear ? (
            <div
              aria-live="polite"
              className="mt-4 rounded-lg bg-rose-300/10 p-3 text-sm text-rose-100"
            >
              <p className="font-semibold">确认清除 Marble Key？</p>
              <p className="mt-1 text-xs leading-5 text-rose-100/80">
                真实模式会同时关闭。此前生成的世界记录和资源不会被删除。
              </p>
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <button
                  className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 disabled:opacity-50"
                  disabled={clearing}
                  onClick={() => setConfirmClear(false)}
                  type="button"
                >
                  保留 Key
                </button>
                <button
                  className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-rose-200 px-3 text-xs font-semibold text-rose-950 transition hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-100 disabled:opacity-50"
                  disabled={clearing}
                  onClick={() => void clearKey()}
                  type="button"
                >
                  {clearing ? (
                    <LoaderCircle
                      aria-hidden="true"
                      className="animate-spin motion-reduce:animate-none"
                      size={14}
                    />
                  ) : null}
                  确认清除
                </button>
              </div>
            </div>
          ) : null}

          {notice ? (
            <div
              aria-live="polite"
              className="mt-4 flex gap-2 rounded-lg bg-emerald-300/10 px-3 py-3 text-sm text-emerald-100"
              role="status"
            >
              <CheckCircle2 aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
              <span>{notice}</span>
            </div>
          ) : null}
          {error ? (
            <div
              aria-live="assertive"
              className="mt-4 flex gap-2 rounded-lg bg-rose-300/10 px-3 py-3 text-sm text-rose-100"
              role="alert"
            >
              <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
              <span>{error}</span>
            </div>
          ) : null}
        </div>

        <aside className="p-5">
          <h3 className="text-sm font-semibold text-white">运行模式</h3>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            Key 与运行模式分开保存，更新凭据不会自动开启真实调用。
          </p>

          <label
            className={`mt-4 flex items-start gap-3 rounded-lg bg-white/[0.035] p-3 ${
              settings.configured && !busy ? "cursor-pointer" : "cursor-not-allowed"
            }`}
          >
            <input
              checked={settings.enabled}
              className="mt-0.5 h-4 w-4 shrink-0 accent-cyan-300"
              disabled={!settings.configured || busy}
              onChange={(event) => void toggleMode(event.target.checked)}
              type="checkbox"
            />
            <span>
              <span className="block text-sm font-semibold text-slate-100">
                启用 Marble 真实模式
              </span>
              <span className="mt-1 block text-xs leading-5 text-slate-400">
                {!settings.configured
                  ? "先验证并保存 Key，之后才能开启。"
                  : togglingMode
                    ? "正在更新运行模式…"
                    : "开启后，新的生成与 PLY 导出可能消耗 credits。"}
              </span>
            </span>
          </label>

          <dl className="mt-5 divide-y divide-white/10 text-sm">
            <div className="flex items-center justify-between gap-4 py-3 first:pt-0">
              <dt className="text-slate-400">当前提供方</dt>
              <dd className="font-medium text-slate-100">
                {settings.enabled ? "Marble" : "Mock"}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-4 py-3">
              <dt className="text-slate-400">已保存凭据</dt>
              <dd className="font-mono text-xs text-slate-200">
                {settings.masked_key || "未保存"}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-4 py-3 last:pb-0">
              <dt className="text-slate-400">剩余 credits</dt>
              <dd className="font-medium text-slate-100">
                {settings.remaining_credits == null
                  ? "验证后显示"
                  : settings.remaining_credits.toLocaleString("zh-CN")}
              </dd>
            </div>
          </dl>
        </aside>
      </div>
    </section>
  );
}
