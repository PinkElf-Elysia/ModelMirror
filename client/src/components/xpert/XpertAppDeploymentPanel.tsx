import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { type XpertAppDefinition, type XpertDefinition } from "../../types/xpert";
import {
  createXpertApp,
  createXpertAppApiKey,
  deployXpertApp,
  disableXpertApp,
  getXpertApp,
  revokeXpertAppApiKey,
  rotateXpertAppShareToken,
  updateXpertApp,
} from "../../utils/xpertApi";

interface Props {
  xpert: XpertDefinition;
}

type BusyAction = "create" | "save" | "deploy" | "disable" | "rotate" | "key" | "";

export default function XpertAppDeploymentPanel({ xpert }: Props) {
  const [app, setApp] = useState<XpertAppDefinition | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<BusyAction>("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedVersion, setSelectedVersion] = useState(xpert.published_version ?? 1);
  const [releaseNotes, setReleaseNotes] = useState("");
  const [keyName, setKeyName] = useState("本地调用");
  const [latestSecret, setLatestSecret] = useState<{ label: string; value: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getXpertApp(xpert.id)
      .then((value) => {
        if (cancelled) return;
        setApp(value);
        if (value?.pinned_version) setSelectedVersion(value.pinned_version);
      })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "App 配置加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [xpert.id]);

  useEffect(() => {
    if (!app?.pinned_version && xpert.published_version) {
      setSelectedVersion(xpert.published_version);
    }
  }, [app?.pinned_version, xpert.published_version]);

  const activeKeys = useMemo(
    () => app?.api_keys.filter((key) => !key.revoked_at) ?? [],
    [app?.api_keys],
  );

  function begin(action: BusyAction) {
    setBusy(action);
    setError("");
    setNotice("");
  }

  async function createApp() {
    begin("create");
    try {
      const result = await createXpertApp(xpert.id, {});
      setApp(result.app);
      setLatestSecret({ label: "分享链接", value: absoluteShareUrl(result.share_url) });
      setNotice("App 草稿已创建。分享 token 仅显示这一次。 ");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "App 创建失败");
    } finally {
      setBusy("");
    }
  }

  async function saveSettings() {
    if (!app) return;
    begin("save");
    try {
      const result = await updateXpertApp(app.app_id, {
        policy: app.policy,
        limits: app.limits,
      });
      setApp(result.app);
      setNotice("访问策略与配额已保存。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "App 设置保存失败");
    } finally {
      setBusy("");
    }
  }

  async function deploy() {
    if (!app) return;
    begin("deploy");
    try {
      const result = await deployXpertApp(app.app_id, {
        version: selectedVersion,
        release_notes: releaseNotes,
      });
      setApp(result.app);
      setReleaseNotes("");
      setNotice(
        result.preflight.warnings.length
          ? `部署完成：${result.preflight.warnings.map((item) => item.message).join("；")}`
          : `v${selectedVersion} 已部署。`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "App 部署失败");
    } finally {
      setBusy("");
    }
  }

  async function disable() {
    if (!app) return;
    begin("disable");
    try {
      const result = await disableXpertApp(app.app_id);
      setApp(result.app);
      setNotice("App 已停用，分享链接和 API key 均不可访问。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "App 停用失败");
    } finally {
      setBusy("");
    }
  }

  async function rotateShareToken() {
    if (!app) return;
    begin("rotate");
    try {
      const result = await rotateXpertAppShareToken(app.app_id);
      setApp(result.app);
      setLatestSecret({ label: "新分享链接", value: absoluteShareUrl(result.share_url) });
      setNotice("分享 token 已轮换，旧链接立即失效。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "分享 token 轮换失败");
    } finally {
      setBusy("");
    }
  }

  async function createApiKey() {
    if (!app || !keyName.trim()) return;
    begin("key");
    try {
      const result = await createXpertAppApiKey(app.app_id, { name: keyName.trim() });
      setApp(result.app);
      setLatestSecret({ label: `API key：${result.key.name}`, value: result.api_key });
      setKeyName("");
      setNotice("API key 已创建，完整值仅显示这一次。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "API key 创建失败");
    } finally {
      setBusy("");
    }
  }

  async function revokeKey(keyId: string) {
    if (!app) return;
    setError("");
    try {
      const result = await revokeXpertAppApiKey(app.app_id, keyId);
      setApp({
        ...app,
        api_keys: app.api_keys.map((key) => key.key_id === keyId ? result.key : key),
      });
      setNotice("API key 已撤销。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "API key 撤销失败");
    }
  }

  if (loading) {
    return <div className="h-40 animate-pulse rounded-lg border border-white/10 bg-white/[0.035]" />;
  }

  if (!app) {
    return (
      <section className="mb-5 rounded-lg border border-white/10 bg-white/[0.035] p-4">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-white">App 与兼容 API</h2>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-400">
              为已发布版本创建未列出分享入口。App 固定版本，后续草稿修改不会影响线上行为。
            </p>
          </div>
          <button
            className="rounded-md bg-hire-300 px-4 py-2 text-xs font-semibold text-ink-950 transition hover:bg-hire-200 disabled:opacity-50"
            disabled={busy === "create"}
            onClick={() => void createApp()}
            type="button"
          >
            {busy === "create" ? "创建中..." : "创建 App 草稿"}
          </button>
        </div>
        {error ? <p className="mt-3 text-xs text-rose-200">{error}</p> : null}
      </section>
    );
  }

  return (
    <section className="mb-5 overflow-hidden rounded-lg border border-white/10 bg-white/[0.035]">
      <div className="flex flex-col gap-3 border-b border-white/10 px-4 py-3 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-white">App 与兼容 API</h2>
            <StatusBadge status={app.status} />
            <span className="text-[11px] text-slate-500">未列出 · {app.slug}</span>
          </div>
          <p className="mt-1 text-xs text-slate-400">
            {app.pinned_version
              ? `线上固定 v${app.pinned_version}，部署 revision ${app.deployment_revision}`
              : "尚未部署，公开凭据当前不可访问。"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {app.status === "active" ? (
            <Link
              className="rounded-md border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-xs font-semibold text-emerald-100 hover:bg-emerald-300/15"
              to={`/apps/${app.slug}`}
            >
              打开分享页
            </Link>
          ) : null}
          <button
            className="rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-semibold text-slate-300 hover:bg-white/[0.08] disabled:opacity-50"
            disabled={Boolean(busy)}
            onClick={() => void saveSettings()}
            type="button"
          >
            {busy === "save" ? "保存中..." : "保存策略"}
          </button>
          {app.status === "active" ? (
            <button
              className="rounded-md border border-rose-300/20 bg-rose-300/[0.06] px-3 py-2 text-xs font-semibold text-rose-100 hover:bg-rose-300/10 disabled:opacity-50"
              disabled={Boolean(busy)}
              onClick={() => void disable()}
              type="button"
            >
              停用 App
            </button>
          ) : null}
        </div>
      </div>

      <div className="grid divide-y divide-white/10 xl:grid-cols-[1.1fr_0.9fr_1fr] xl:divide-x xl:divide-y-0">
        <div className="p-4">
          <h3 className="text-xs font-semibold text-white">部署版本</h3>
          <div className="mt-3 flex gap-2">
            <select
              className="min-w-0 flex-1 rounded-md border border-white/10 bg-ink-950/70 px-3 py-2 text-xs text-white outline-none focus:border-hire-300/60"
              onChange={(event) => setSelectedVersion(Number(event.target.value))}
              value={selectedVersion}
            >
              {xpert.versions.map((version) => (
                <option className="bg-ink-950" key={version.version} value={version.version}>
                  v{version.version} · revision {version.draft_revision}
                </option>
              ))}
            </select>
            <button
              className="rounded-md bg-hire-300 px-3 py-2 text-xs font-semibold text-ink-950 hover:bg-hire-200 disabled:opacity-50"
              disabled={Boolean(busy) || xpert.versions.length === 0}
              onClick={() => void deploy()}
              type="button"
            >
              {busy === "deploy" ? "部署中..." : app.pinned_version ? "部署版本" : "首次部署"}
            </button>
          </div>
          <input
            className="mt-2 w-full rounded-md border border-white/10 bg-ink-950/55 px-3 py-2 text-xs text-slate-200 outline-none focus:border-hire-300/60"
            maxLength={1000}
            onChange={(event) => setReleaseNotes(event.target.value)}
            placeholder="部署说明（可选）"
            value={releaseNotes}
          />
          <div className="mt-3 max-h-28 space-y-1.5 overflow-y-auto">
            {[...app.deployments].reverse().slice(0, 6).map((deployment) => (
              <div className="flex items-center justify-between rounded-md bg-white/[0.035] px-2.5 py-2 text-[11px]" key={deployment.revision}>
                <span className="font-semibold text-slate-200">r{deployment.revision} · v{deployment.version}</span>
                <span className="text-slate-500">{new Date(deployment.deployed_at * 1000).toLocaleDateString("zh-CN")}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="p-4">
          <h3 className="text-xs font-semibold text-white">执行边界</h3>
          <div className="mt-3 space-y-2">
            <PolicyToggle checked={app.policy.allow_tools} label="允许 MCP 工具" onChange={(checked) => setApp({ ...app, policy: { ...app.policy, allow_tools: checked } })} />
            <PolicyToggle checked={app.policy.allow_handoffs} label="允许智能体任务交接" onChange={(checked) => setApp({ ...app, policy: { ...app.policy, allow_handoffs: checked } })} />
            <PolicyToggle checked={app.policy.allow_xpert_memory} label="允许读取智能体记忆" onChange={(checked) => setApp({ ...app, policy: { ...app.policy, allow_xpert_memory: checked } })} />
            <PolicyToggle checked={app.policy.allow_knowledge_read} label="允许只读知识工具" onChange={(checked) => setApp({ ...app, policy: { ...app.policy, allow_knowledge_read: checked } })} />
            <PolicyToggle checked={app.policy.allow_datax_read} label="允许只读 Data X 指标" onChange={(checked) => setApp({ ...app, policy: { ...app.policy, allow_datax_read: checked } })} />
          </div>
          <p className="mt-3 text-[11px] leading-5 text-slate-500">
            工具与 Data X 模式还要求已发布工作流包含 tool_policy。公开 App 不上传附件、不创建指标提案，也不生成记忆候选。
          </p>
          <div className="mt-3 grid grid-cols-3 gap-2">
            <LimitInput label="RPM" value={app.limits.requests_per_minute} onChange={(value) => setApp({ ...app, limits: { ...app.limits, requests_per_minute: value } })} />
            <LimitInput label="每日" value={app.limits.requests_per_day} onChange={(value) => setApp({ ...app, limits: { ...app.limits, requests_per_day: value } })} />
            <LimitInput label="并发" value={app.limits.max_concurrency} onChange={(value) => setApp({ ...app, limits: { ...app.limits, max_concurrency: value } })} />
          </div>
        </div>

        <div className="p-4">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-xs font-semibold text-white">访问凭据</h3>
            <button
              className="text-[11px] font-semibold text-cyan-200 hover:text-cyan-100 disabled:opacity-50"
              disabled={Boolean(busy)}
              onClick={() => void rotateShareToken()}
              type="button"
            >
              {busy === "rotate" ? "轮换中..." : "轮换分享链接"}
            </button>
          </div>
          <p className="mt-2 text-[11px] text-slate-500">分享 token：{app.share_token_prefix}...</p>
          <div className="mt-3 flex gap-2">
            <input
              className="min-w-0 flex-1 rounded-md border border-white/10 bg-ink-950/55 px-3 py-2 text-xs text-slate-200 outline-none focus:border-hire-300/60"
              maxLength={80}
              onChange={(event) => setKeyName(event.target.value)}
              placeholder="API key 名称"
              value={keyName}
            />
            <button
              className="rounded-md border border-white/10 bg-white/[0.05] px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-white/[0.08] disabled:opacity-50"
              disabled={Boolean(busy) || !keyName.trim()}
              onClick={() => void createApiKey()}
              type="button"
            >
              {busy === "key" ? "创建中..." : "创建 key"}
            </button>
          </div>
          <div className="mt-3 max-h-28 space-y-1.5 overflow-y-auto">
            {activeKeys.length ? activeKeys.map((key) => (
              <div className="flex items-center gap-2 rounded-md bg-white/[0.035] px-2.5 py-2" key={key.key_id}>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[11px] font-semibold text-slate-200">{key.name}</p>
                  <p className="text-[10px] text-slate-500">{key.prefix}... · 今日 {key.requests_today}</p>
                </div>
                <button className="text-[10px] text-rose-200 hover:text-rose-100" onClick={() => void revokeKey(key.key_id)} type="button">撤销</button>
              </div>
            )) : <p className="rounded-md border border-dashed border-white/10 p-3 text-center text-[11px] text-slate-500">尚未创建 API key</p>}
          </div>
        </div>
      </div>

      {latestSecret ? (
        <div className="border-t border-amber-300/20 bg-amber-300/[0.06] px-4 py-3">
          <div className="flex flex-col gap-2 md:flex-row md:items-center">
            <div className="min-w-0 flex-1">
              <p className="text-[11px] font-semibold text-amber-100">{latestSecret.label}，关闭后无法再次查看</p>
              <code className="mt-1 block break-all text-[11px] text-amber-50/85">{latestSecret.value}</code>
            </div>
            <button className="rounded-md border border-amber-200/25 px-3 py-2 text-xs font-semibold text-amber-100" onClick={() => void navigator.clipboard.writeText(latestSecret.value)} type="button">复制凭据</button>
            <button className="rounded-md px-3 py-2 text-xs text-slate-400" onClick={() => setLatestSecret(null)} type="button">我已保存</button>
          </div>
        </div>
      ) : null}
      {notice || error ? (
        <p className={`border-t px-4 py-2.5 text-xs ${error ? "border-rose-300/20 bg-rose-300/[0.06] text-rose-100" : "border-emerald-300/20 bg-emerald-300/[0.06] text-emerald-100"}`}>
          {error || notice}
        </p>
      ) : null}
    </section>
  );
}

function absoluteShareUrl(path: string) {
  return `${window.location.origin}${path}`;
}

function StatusBadge({ status }: { status: XpertAppDefinition["status"] }) {
  const copy = status === "active" ? "运行中" : status === "disabled" ? "已停用" : "草稿";
  const style = status === "active"
    ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
    : status === "disabled"
      ? "border-rose-300/20 bg-rose-300/[0.06] text-rose-100"
      : "border-white/10 bg-white/[0.04] text-slate-300";
  return <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${style}`}>{copy}</span>;
}

function PolicyToggle({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 rounded-md bg-white/[0.035] px-3 py-2 text-xs text-slate-300">
      <span>{label}</span>
      <input checked={checked} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
    </label>
  );
}

function LimitInput({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="text-[10px] text-slate-500">
      {label}
      <input
        className="mt-1 h-8 w-full rounded-md border border-white/10 bg-ink-950/55 px-2 text-xs text-white outline-none focus:border-hire-300/60"
        min={1}
        onChange={(event) => onChange(Math.max(1, Number(event.target.value) || 1))}
        type="number"
        value={value}
      />
    </label>
  );
}
