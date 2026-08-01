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
    const payload = (await response.json()) as { detail?: string };
    return payload.detail || `请求失败（${response.status}）`;
  } catch {
    return `请求失败（${response.status}）`;
  }
}

export default function MarbleConnectionSettings() {
  const [settings, setSettings] = useState<MarbleSettings>(EMPTY_SETTINGS);
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/world-generations/settings/marble")
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return (await response.json()) as MarbleSettings;
      })
      .then((value) => {
        if (cancelled) return;
        setSettings(value);
        setEnabled(value.enabled);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Marble 设置加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveSettings() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch("/api/world-generations/settings/marble", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_key: apiKey.trim() || undefined,
          enabled,
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const value = (await response.json()) as MarbleSettings;
      setSettings(value);
      setEnabled(value.enabled);
      setApiKey("");
      setMessage(
        value.enabled
          ? "Key 已验证并保存，Marble 真实模式已启用。"
          : "Key 已验证并保存，当前仍使用 Mock 模式。",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Marble 设置保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function clearSettings() {
    if (!window.confirm("确认清除已保存的 Marble Key 并关闭真实模式？")) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch("/api/world-generations/settings/marble", {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(await readError(response));
      const value = (await response.json()) as MarbleSettings;
      setSettings(value);
      setEnabled(false);
      setApiKey("");
      setMessage("Marble Key 已清除，已切回 Mock 模式。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Marble Key 清除失败");
    } finally {
      setSaving(false);
    }
  }

  const canSave = !loading && !saving && (settings.configured || apiKey.trim().length > 0);

  return (
    <section className="mb-6 overflow-hidden rounded-lg border border-cyan-300/20 bg-ink-950/82 shadow-prism">
      <div className="flex flex-col gap-4 border-b border-white/10 bg-[linear-gradient(110deg,rgba(8,145,178,0.13),rgba(255,255,255,0.025))] px-5 py-5 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-200">
            World Generation
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-white">Marble 真实服务</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            在服务端加密保存 World Labs Marble API Key。保存时会查询 credits
            验证连接，不会创建世界；只有开启真实模式并提交生成任务后才可能产生费用。
          </p>
        </div>
        <span
          className={`inline-flex w-fit items-center rounded-full border px-3 py-1.5 text-xs font-semibold ${
            settings.enabled
              ? "border-emerald-300/30 bg-emerald-300/10 text-emerald-200"
              : settings.configured
                ? "border-amber-300/30 bg-amber-300/10 text-amber-100"
                : "border-white/10 bg-white/5 text-slate-400"
          }`}
        >
          {loading
            ? "正在读取…"
            : settings.enabled
              ? "真实模式已启用"
              : settings.configured
                ? "Key 已配置 · Mock 模式"
                : "尚未配置"}
        </span>
      </div>

      <div className="grid gap-5 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div>
          <label className="text-sm font-medium text-slate-200" htmlFor="marble-api-key">
            Marble API Key
          </label>
          <div className="mt-2 flex overflow-hidden rounded-lg border border-white/10 bg-black/20 focus-within:border-cyan-300/45">
            <input
              autoComplete="off"
              className="min-w-0 flex-1 bg-transparent px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-600"
              id="marble-api-key"
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={settings.masked_key || "粘贴 World Labs API Key"}
              type={showKey ? "text" : "password"}
              value={apiKey}
            />
            <button
              className="border-l border-white/10 px-3 text-xs font-semibold text-slate-400 hover:text-white"
              onClick={() => setShowKey((value) => !value)}
              type="button"
            >
              {showKey ? "隐藏" : "显示"}
            </button>
          </div>
          <p className="mt-2 text-xs leading-5 text-slate-500">
            Key 仅发送给本机后端并加密存储，页面不会读取或回显明文。
          </p>

          <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-lg border border-white/10 bg-white/[0.035] p-3">
            <input
              checked={enabled}
              className="mt-0.5 h-4 w-4 accent-cyan-400"
              disabled={loading || saving}
              onChange={(event) => setEnabled(event.target.checked)}
              type="checkbox"
            />
            <span>
              <span className="block text-sm font-semibold text-white">启用 Marble 真实模式</span>
              <span className="mt-1 block text-xs leading-5 text-amber-100/70">
                开启后，世界生成会调用真实 Marble API；生成与 PLY 导出可能消耗 credits。
              </span>
            </span>
          </label>

          {error ? <p className="mt-3 text-sm text-rose-300">{error}</p> : null}
          {message ? <p className="mt-3 text-sm text-emerald-300">{message}</p> : null}

          <div className="mt-5 flex flex-wrap gap-3">
            <button
              className="rounded-full bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={!canSave}
              onClick={() => void saveSettings()}
              type="button"
            >
              {saving ? "正在验证…" : "验证并保存"}
            </button>
            {settings.configured ? (
              <button
                className="rounded-full border border-rose-300/25 px-4 py-2 text-sm font-semibold text-rose-200 transition hover:bg-rose-300/10 disabled:opacity-40"
                disabled={saving}
                onClick={() => void clearSettings()}
                type="button"
              >
                清除 Key
              </button>
            ) : null}
          </div>
        </div>

        <aside className="rounded-lg border border-white/10 bg-black/20 p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
            Connection status
          </p>
          <dl className="mt-4 space-y-3 text-sm">
            <div className="flex items-center justify-between gap-4">
              <dt className="text-slate-500">凭据</dt>
              <dd className="font-medium text-slate-200">
                {settings.masked_key || "未保存"}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-4">
              <dt className="text-slate-500">剩余 credits</dt>
              <dd className="font-medium text-slate-200">
                {settings.remaining_credits == null
                  ? "验证后显示"
                  : settings.remaining_credits.toLocaleString()}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-4">
              <dt className="text-slate-500">当前提供方</dt>
              <dd className={settings.enabled ? "text-emerald-300" : "text-slate-300"}>
                {settings.enabled ? "Marble" : "Mock"}
              </dd>
            </div>
          </dl>
        </aside>
      </div>
    </section>
  );
}
