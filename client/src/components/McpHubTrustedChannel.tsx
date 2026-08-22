import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleOff,
  RefreshCw,
  Search,
  ShieldCheck,
  Unplug,
} from "lucide-react";

type AvailabilityState =
  | "ready"
  | "stale"
  | "degraded"
  | "environment_blocked"
  | "drifted"
  | "revoked"
  | "collision";

interface TrustedStatus {
  enabled: boolean;
  auto_review_enabled: boolean;
  health_ttl_seconds: number;
  total: number;
  counts: Record<string, number>;
}

interface TrustedServer {
  contract_id: string;
  contract_fingerprint: string;
  contract_source: "repository" | "local";
  server_name: string;
  version: string;
  title: string;
  description: string;
  publisher: string;
  categories: string[];
  origin: string;
  allowed_tools: string[];
  availability_state: AvailabilityState;
  health_checked_at: number;
  health_error_code: string;
  candidate_id: string;
  candidate_state: string;
  connected: boolean;
}

const stateCopy: Record<AvailabilityState, { label: string; detail: string; tone: string }> = {
  ready: {
    label: "已复核可用",
    detail: "远程身份和工具 Schema 已通过当前隔离检查。",
    tone: "border-emerald-300/25 bg-emerald-300/10 text-emerald-100",
  },
  stale: {
    label: "需要实时复核",
    detail: "契约仍有效，连接前会重新校验远程身份和工具 Schema。",
    tone: "border-amber-300/25 bg-amber-300/10 text-amber-100",
  },
  degraded: {
    label: "远程暂不可达",
    detail: "最近检查遇到超时、限流或连接失败，契约未被撤销。",
    tone: "border-amber-300/25 bg-amber-300/10 text-amber-100",
  },
  environment_blocked: {
    label: "当前环境已阻断",
    detail: "本机 DNS 或隔离出口拒绝了连接，这不代表远程服务存在安全问题。",
    tone: "border-sky-300/25 bg-sky-300/10 text-sky-100",
  },
  drifted: {
    label: "契约已漂移",
    detail: "Registry 身份、来源或工具 Schema 已变化，必须重新进入复核流程。",
    tone: "border-rose-300/25 bg-rose-300/10 text-rose-100",
  },
  revoked: {
    label: "已撤销",
    detail: "本地运维者已撤销该执行契约。",
    tone: "border-rose-300/25 bg-rose-300/10 text-rose-100",
  },
  collision: {
    label: "契约冲突",
    detail: "同一身份出现不同执行指纹，系统已关闭该条目。",
    tone: "border-rose-300/25 bg-rose-300/10 text-rose-100",
  },
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: { code?: string; error?: string } | string;
  } & T;
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.error || "可信 Hub 请求失败",
    );
  }
  return payload;
}

function jsonRequest(expectedFingerprint: string): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_contract_fingerprint: expectedFingerprint }),
  };
}

function formatCheckedAt(value: number): string {
  if (!value) return "尚未检查";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

export default function McpHubTrustedChannel({
  onChanged,
  refreshToken = 0,
}: {
  onChanged?: () => void | Promise<void>;
  refreshToken?: number;
}) {
  const [status, setStatus] = useState<TrustedStatus | null>(null);
  const [items, setItems] = useState<TrustedServer[]>([]);
  const [query, setQuery] = useState("");
  const [state, setState] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const current = await requestJson<TrustedStatus>(
        "/api/mcp/hub/trusted/status",
      );
      setStatus(current);
      if (!current.enabled) {
        setItems([]);
        return;
      }
      const params = new URLSearchParams({ limit: "100" });
      if (query.trim()) params.set("q", query.trim());
      if (state) params.set("state", state);
      const result = await requestJson<{ items: TrustedServer[] }>(
        `/api/mcp/hub/trusted/servers?${params}`,
      );
      setItems(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "可信频道加载失败");
    }
  }, [query, state]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshToken]);

  const run = async (
    key: string,
    operation: () => Promise<unknown>,
    success: string,
  ) => {
    setBusy(key);
    setError("");
    setNotice("");
    try {
      await operation();
      await refresh();
      await onChanged?.();
      setNotice(success);
    } catch (reason) {
      const operationError = reason instanceof Error ? reason.message : "可信频道操作失败";
      await refresh();
      setError(operationError);
    } finally {
      setBusy("");
    }
  };

  if (!status) {
    return (
      <section aria-busy="true" className="rounded-xl border border-white/10 bg-ink-900/60 p-5">
        <div className="h-5 w-48 animate-pulse rounded bg-white/10" />
        <div className="mt-3 h-4 w-full max-w-xl animate-pulse rounded bg-white/[0.06]" />
      </section>
    );
  }

  if (!status.enabled) return null;

  return (
    <section aria-labelledby="trusted-hub-heading" className="overflow-hidden rounded-xl border border-emerald-300/15 bg-ink-900/70">
      <header className="border-b border-white/10 p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2 text-white">
              <ShieldCheck aria-hidden="true" className="text-emerald-200" size={20} />
              <h3 className="text-lg font-semibold" id="trusted-hub-heading">可信可用</h3>
            </div>
            <p className="mt-2 text-sm leading-6 text-slate-300">
              这些服务已冻结版本、来源和工具 Schema。连接仍会执行实时隔离检查，实际工具调用继续逐次审批。
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-300">
            <span className="rounded-full border border-emerald-300/20 bg-emerald-300/10 px-2.5 py-1">
              {status.counts.ready || 0} 项当前可用
            </span>
            <span className="rounded-full border border-white/10 px-2.5 py-1">
              {status.total} 项已复核
            </span>
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <label className="relative min-w-0 flex-1">
            <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
            <span className="sr-only">搜索可信 MCP</span>
            <input
              className="min-h-10 w-full rounded-lg border border-white/10 bg-ink-950/70 pl-9 pr-3 text-sm text-white placeholder:text-slate-400"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索名称、发布者或用途"
              type="search"
              value={query}
            />
          </label>
          <label>
            <span className="sr-only">按可用状态筛选</span>
            <select
              className="modelmirror-form-control min-h-10 rounded-lg border border-white/10 bg-ink-950/70 px-3 text-sm text-slate-200"
              onChange={(event) => setState(event.target.value)}
              value={state}
            >
              <option value="">全部状态</option>
              <option value="ready">已复核可用</option>
              <option value="stale">需要实时复核</option>
              <option value="degraded">远程暂不可达</option>
              <option value="environment_blocked">当前环境已阻断</option>
              <option value="drifted">契约已漂移</option>
              <option value="revoked">已撤销</option>
              <option value="collision">契约冲突</option>
            </select>
          </label>
        </div>

        {error ? (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100" role="alert">
            <AlertTriangle aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
            <span>{error}</span>
          </div>
        ) : null}
        {notice ? (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-sm text-emerald-100" role="status">
            <CheckCircle2 aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
            <span>{notice}</span>
          </div>
        ) : null}
      </header>

      <div className="divide-y divide-white/10">
        {items.map((item) => {
          const stateInfo = stateCopy[item.availability_state];
          const active = item.candidate_state === "active";
          const connectable = item.availability_state === "ready" || item.availability_state === "stale";
          const recheckable = ["degraded", "environment_blocked"].includes(item.availability_state);
          return (
            <article className="p-4 transition-colors duration-200 hover:bg-white/[0.025] sm:p-5" key={item.contract_id}>
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0 max-w-3xl">
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="font-semibold text-white">{item.title}</h4>
                    <span className={`rounded-full border px-2 py-0.5 text-xs ${stateInfo.tone}`}>
                      {stateInfo.label}
                    </span>
                    <span className="rounded-full border border-white/10 px-2 py-0.5 text-xs text-slate-300">
                      {item.contract_source === "repository" ? "随应用发布" : "本机复核"}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-400">
                    {item.publisher || item.server_name} · {item.version} · {item.origin}
                  </p>
                  {item.description ? (
                    <p className="mt-2 line-clamp-2 text-sm leading-6 text-slate-300">{item.description}</p>
                  ) : null}
                  <p className="mt-2 text-xs leading-5 text-slate-400">{stateInfo.detail}</p>
                  <div className="mt-3 flex flex-wrap gap-1.5" aria-label="允许的工具">
                    {item.allowed_tools.slice(0, 8).map((tool) => (
                      <span className="rounded border border-white/10 bg-ink-950/60 px-2 py-1 font-mono text-[11px] text-cyan-100" key={tool}>{tool}</span>
                    ))}
                    {item.allowed_tools.length > 8 ? (
                      <span className="px-1 py-1 text-xs text-slate-400">另有 {item.allowed_tools.length - 8} 项</span>
                    ) : null}
                  </div>
                  <p className="mt-3 text-xs text-slate-500">
                    最近检查：{formatCheckedAt(item.health_checked_at)}
                    {item.health_error_code ? ` · ${item.health_error_code}` : ""}
                  </p>
                </div>

                <div className="flex shrink-0 flex-wrap items-center gap-2 lg:justify-end">
                  {active ? (
                    <span className="flex min-h-9 items-center gap-1.5 rounded-lg border border-emerald-300/20 px-3 text-sm text-emerald-100">
                      <CheckCircle2 aria-hidden="true" size={15} />已加入我的 MCP
                    </span>
                  ) : null}
                  {!active && connectable ? (
                    <button
                      className="flex min-h-9 items-center gap-1.5 rounded-lg bg-emerald-300 px-3 text-sm font-semibold text-ink-950 transition-colors duration-200 hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-45"
                      disabled={Boolean(busy)}
                      onClick={() => void run(
                        `activate:${item.contract_id}`,
                        () => requestJson(`/api/mcp/hub/trusted/servers/${item.contract_id}/activate`, jsonRequest(item.contract_fingerprint)),
                        `${item.title} 已加入“我的 Hub 连接”。`,
                      )}
                      type="button"
                    >
                      {busy === `activate:${item.contract_id}` ? <RefreshCw aria-hidden="true" className="animate-spin" size={15} /> : <ShieldCheck aria-hidden="true" size={15} />}
                      {item.availability_state === "stale" ? "复核并连接" : "连接服务"}
                    </button>
                  ) : null}
                  {!active && recheckable ? (
                    <button
                      className="flex min-h-9 items-center gap-1.5 rounded-lg border border-cyan-300/25 px-3 text-sm font-semibold text-cyan-100 transition-colors duration-200 hover:bg-cyan-300/10 disabled:cursor-not-allowed disabled:opacity-45"
                      disabled={Boolean(busy)}
                      onClick={() => void run(
                        `check:${item.contract_id}`,
                        () => requestJson(`/api/mcp/hub/trusted/servers/${item.contract_id}/revalidate`, jsonRequest(item.contract_fingerprint)),
                        `${item.title} 已完成重新检查。`,
                      )}
                      type="button"
                    >
                      <RefreshCw aria-hidden="true" className={busy === `check:${item.contract_id}` ? "animate-spin" : ""} size={15} />
                      重新检查
                    </button>
                  ) : null}
                  {!active && !connectable && !recheckable ? (
                    <span className="flex min-h-9 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-sm text-slate-400">
                      {item.availability_state === "revoked" ? <Unplug aria-hidden="true" size={15} /> : <CircleOff aria-hidden="true" size={15} />}
                      当前不可连接
                    </span>
                  ) : null}
                </div>
              </div>
            </article>
          );
        })}
        {items.length === 0 ? (
          <div className="p-8 text-center">
            <ShieldCheck aria-hidden="true" className="mx-auto text-slate-500" size={28} />
            <p className="mt-3 font-medium text-slate-200">没有匹配的可信服务</p>
            <p className="mt-1 text-sm text-slate-400">调整筛选条件，或由本地运维者完成新的受控复核。</p>
          </div>
        ) : null}
      </div>
    </section>
  );
}
