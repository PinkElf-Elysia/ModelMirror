import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw, Search, Shield, Trash2 } from "lucide-react";

type Eligibility =
  | "eligible"
  | "auth_required"
  | "local_runtime"
  | "legacy_transport"
  | "removed"
  | "no_remote";

interface HubStatus {
  enabled: boolean;
  remote_enabled: boolean;
  source: string;
  snapshot_at: number;
  snapshot_count: number;
  last_sync_skipped_count?: number;
}

interface HubRemote {
  remote_id: string;
  transport: string;
  origin: string;
  eligibility: Eligibility;
  reason: string;
}

interface HubServer {
  server_name: string;
  version: string;
  title: string;
  description: string;
  publisher?: string;
  categories?: string[];
  status: string;
  eligibility: Eligibility;
  remotes: HubRemote[];
}

interface HubTool {
  name: string;
  description: string;
  schema_digest: string;
}

interface HubCandidate {
  candidate_id: string;
  server_name: string;
  version: string;
  state: string;
  origin: string;
  schema_digest: string;
  tools: HubTool[];
  connected: boolean;
  taint_reason: string;
  activation_eligible: boolean;
  activation_reason: string;
}

const activationReasonLabels: Record<string, string> = {
  hub_contract_unreviewed: "该候选尚未完成 ModelMirror 执行契约复核，只可预检。",
  hub_preflight_required: "请先完成安全预检。",
  hub_reviewed_contract_drift: "远程 Schema 与已复核契约不一致，禁止激活。",
};

const eligibilityLabels: Record<Eligibility, string> = {
  eligible: "可试连",
  auth_required: "需认证",
  local_runtime: "本地运行时",
  legacy_transport: "旧传输",
  removed: "已下架",
  no_remote: "无可用远程端点",
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: { error?: string } | string;
  } & T;
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.error || "MCP Hub 请求失败",
    );
  }
  return payload;
}

export default function McpHubPanel() {
  const [status, setStatus] = useState<HubStatus | null>(null);
  const [servers, setServers] = useState<HubServer[]>([]);
  const [candidates, setCandidates] = useState<HubCandidate[]>([]);
  const [query, setQuery] = useState("");
  const [eligibility, setEligibility] = useState("");
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<string[]>([]);
  const [page, setPage] = useState(0);
  const [serverTotal, setServerTotal] = useState(0);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const candidateSectionRef = useRef<HTMLElement>(null);

  const refresh = useCallback(async () => {
    setError("");
    try {
      const current = await requestJson<HubStatus>("/api/mcp/hub/status");
      setStatus(current);
      if (!current.enabled) {
        setServers([]);
        setCandidates([]);
        return;
      }
      const params = new URLSearchParams({ limit: "50" });
      params.set("cursor", String(page * 50));
      if (query.trim()) params.set("q", query.trim());
      if (category) params.set("category", category);
      if (eligibility) params.set("eligibility", eligibility);
      const [serverData, candidateData] = await Promise.all([
        requestJson<{ items: HubServer[]; categories: string[]; total: number; next_cursor: number | null }>(`/api/mcp/hub/servers?${params}`),
        requestJson<{ items: HubCandidate[] }>("/api/mcp/hub/candidates"),
      ]);
      setServers(serverData.items);
      setCategories(serverData.categories || []);
      setServerTotal(serverData.total || 0);
      setHasNextPage(serverData.next_cursor !== null);
      setCandidates(candidateData.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "MCP Hub 加载失败");
    }
  }, [category, eligibility, page, query]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async <T,>(
    key: string,
    operation: () => Promise<T>,
    onSuccess?: (result: T) => void,
  ) => {
    setBusy(key);
    setError("");
    setNotice("");
    try {
      const result = await operation();
      await refresh();
      onSuccess?.(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "MCP Hub 操作失败");
    } finally {
      setBusy("");
    }
  };

  const showAddedCandidate = (title: string) => {
    setNotice(`${title} 已添加到“我的 Hub 连接”，可以继续安全预检。`);
    window.requestAnimationFrame(() => {
      const section = candidateSectionRef.current;
      if (!section) return;
      const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      section.scrollIntoView?.({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
      section.focus({ preventScroll: true });
    });
  };

  const sync = () =>
    run("sync", async () => {
      const job = await requestJson<{ sync_id: string }>("/api/mcp/hub/sync", {
        method: "POST",
      });
      for (let attempt = 0; attempt < 600; attempt += 1) {
        const result = await requestJson<{ status: string; error_code: string }>(
          `/api/mcp/hub/sync/${encodeURIComponent(job.sync_id)}`,
        );
        if (result.status === "completed" || result.status === "not_modified") return;
        if (result.status === "failed") {
          throw new Error(`Registry 同步失败：${result.error_code || "unknown"}`);
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
      throw new Error("Registry 同步仍在进行，请稍后刷新");
    });

  if (!status) {
    return <div className="rounded-lg border border-white/10 bg-white/[0.035] p-6 text-sm text-slate-400">正在读取 MCP Hub 状态…</div>;
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-white/10 bg-ink-900/60 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-white">
              <Shield aria-hidden="true" className="text-hire-200" size={19} />
              <h3 className="font-semibold">官方 Registry 受控发现</h3>
            </div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              Registry 收录不代表安全认证。这里只允许匿名、固定公网 HTTPS 的 Streamable HTTP 端点；客户端不会提交 URL、命令、Header、环境变量或凭据。
            </p>
          </div>
          <button
            className="flex min-h-10 items-center gap-2 rounded-lg border border-hire-300/30 bg-hire-300/10 px-3 py-2 text-sm font-semibold text-hire-100 disabled:opacity-50"
            disabled={!status.enabled || busy === "sync"}
            onClick={() => void sync()}
            type="button"
          >
            <RefreshCw aria-hidden="true" className={busy === "sync" ? "animate-spin" : ""} size={16} />
            同步官方 Registry
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-400">
          <span className="rounded-md border border-white/10 px-2 py-1">功能：{status.enabled ? "已启用" : "默认关闭"}</span>
          <span className="rounded-md border border-white/10 px-2 py-1">远程试连：{status.remote_enabled ? "已启用" : "默认关闭"}</span>
          <span className="rounded-md border border-white/10 px-2 py-1">快照：{status.snapshot_count} 项</span>
          {status.last_sync_skipped_count ? <span className="rounded-md border border-amber-300/20 px-2 py-1 text-amber-100">拒绝异常记录：{status.last_sync_skipped_count}</span> : null}
          <span className="rounded-md border border-white/10 px-2 py-1">
            {status.snapshot_at ? new Date(status.snapshot_at * 1000).toLocaleString() : "尚未同步"}
          </span>
        </div>
      </section>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-100" role="alert">
          <AlertTriangle aria-hidden="true" className="mt-0.5 shrink-0" size={17} />
          {error}
        </div>
      ) : null}

      {!status.enabled ? (
        <div className="rounded-lg border border-amber-300/20 bg-amber-300/10 p-5 text-sm leading-6 text-amber-100">
          MCP Hub 功能开关默认关闭。启用后仍需单独开启远程试连，且所有工具调用都必须逐次审批。
        </div>
      ) : (
        <>
          <section className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
            <div className="grid gap-3 lg:grid-cols-[1fr_200px_220px]">
              <label className="relative">
                <span className="sr-only">搜索官方 Registry</span>
                <Search aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={17} />
                <input
                  className="min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/70 pl-10 pr-3 text-sm text-white outline-none focus:border-hire-300/50"
                  onChange={(event) => {
                    setPage(0);
                    setQuery(event.target.value);
                  }}
                  placeholder="搜索名称或用途"
                  type="search"
                  value={query}
                />
              </label>
              <select
                aria-label="按 Registry 分类筛选"
                className="min-h-11 rounded-lg border border-white/10 bg-ink-950/70 px-3 text-sm text-white"
                onChange={(event) => {
                  setPage(0);
                  setCategory(event.target.value);
                }}
                value={category}
              >
                <option value="">全部分类</option>
                {categories.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
              <select
                aria-label="按 Hub 准入状态筛选"
                className="min-h-11 rounded-lg border border-white/10 bg-ink-950/70 px-3 text-sm text-white"
                onChange={(event) => {
                  setPage(0);
                  setEligibility(event.target.value);
                }}
                value={eligibility}
              >
                <option value="">全部准入状态</option>
                {Object.entries(eligibilityLabels).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {servers.map((server) => {
                const eligible = server.remotes.find((remote) => remote.eligibility === "eligible");
                const alreadyAdded = Boolean(
                  eligible && candidates.some((candidate) => (
                    candidate.server_name === server.server_name &&
                    candidate.version === server.version &&
                    candidate.origin === eligible.origin
                  )),
                );
                return (
                  <article className="rounded-lg border border-white/10 bg-ink-950/45 p-4" key={`${server.server_name}:${server.version}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h4 className="truncate font-semibold text-white">{server.title}</h4>
                        <p className="mt-1 break-all font-mono text-xs text-slate-500">{server.server_name}@{server.version}</p>
                        {server.publisher ? <p className="mt-1 text-xs text-slate-400">Publisher：{server.publisher}</p> : null}
                      </div>
                      <span className={`shrink-0 rounded-md border px-2 py-1 text-xs font-semibold ${server.eligibility === "eligible" ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100" : "border-white/10 text-slate-400"}`}>
                        {eligibilityLabels[server.eligibility]}
                      </span>
                    </div>
                    <p className="mt-3 line-clamp-3 text-sm leading-6 text-slate-400">{server.description || "暂无描述"}</p>
                    {server.categories?.length ? <p className="mt-2 text-xs text-slate-500">分类：{server.categories.join("、")}</p> : null}
                    {eligible ? <p className="mt-2 break-all text-xs text-cyan-100">Origin：{eligible.origin}</p> : null}
                    <button
                      className="mt-3 min-h-10 rounded-lg bg-hire-300 px-3 py-2 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40"
                      disabled={!eligible || !status.remote_enabled || alreadyAdded || Boolean(busy)}
                      onClick={() => eligible && void run(`add:${server.server_name}`, () => requestJson("/api/mcp/hub/candidates", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ server_name: server.server_name, version: server.version, remote_id: eligible.remote_id }),
                      }), () => showAddedCandidate(server.title || server.server_name))}
                      type="button"
                    >
                      {alreadyAdded ? "已添加" : "添加到我的 MCP"}
                    </button>
                  </article>
                );
              })}
            </div>
            {servers.length === 0 ? <p className="py-8 text-center text-sm text-slate-400">当前快照没有匹配条目。</p> : null}
            {serverTotal > 0 ? (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-4 text-xs text-slate-400">
                <span>共 {serverTotal} 项 · 第 {page + 1} 页</span>
                <div className="flex gap-2">
                  <button
                    className="min-h-9 rounded-md border border-white/10 px-3 font-semibold text-slate-200 disabled:opacity-40"
                    disabled={page === 0 || Boolean(busy)}
                    onClick={() => setPage((current) => Math.max(0, current - 1))}
                    type="button"
                  >
                    上一页
                  </button>
                  <button
                    className="min-h-9 rounded-md border border-white/10 px-3 font-semibold text-slate-200 disabled:opacity-40"
                    disabled={!hasNextPage || Boolean(busy)}
                    onClick={() => setPage((current) => current + 1)}
                    type="button"
                  >
                    下一页
                  </button>
                </div>
              </div>
            ) : null}
          </section>

          <section
            aria-labelledby="hub-candidates-heading"
            className="rounded-xl border border-white/10 bg-white/[0.025] p-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-hire-200/70"
            ref={candidateSectionRef}
            tabIndex={-1}
          >
            <h3 className="text-lg font-semibold text-white" id="hub-candidates-heading">我的 Hub 连接</h3>
            {notice ? (
              <div className="mt-3 flex items-start gap-2 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-4 py-3 text-sm text-emerald-100" role="status">
                <CheckCircle2 aria-hidden="true" className="mt-0.5 shrink-0" size={17} />
                {notice}
              </div>
            ) : null}
            <div className="mt-3 space-y-3">
              {candidates.map((candidate) => (
                <article className="rounded-lg border border-white/10 bg-ink-950/45 p-4" key={candidate.candidate_id}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <h4 className="font-semibold text-white">{candidate.server_name}</h4>
                        {candidate.state === "active" ? <CheckCircle2 aria-label="已激活" className="text-emerald-200" size={16} /> : null}
                      </div>
                      <p className="mt-1 text-xs text-slate-500">{candidate.version} · {candidate.origin}</p>
                      <p className="mt-2 text-sm text-slate-300">状态：{candidate.state} · {candidate.connected ? "已连接" : "未连接"}</p>
                    </div>
                    <button
                      aria-label="删除 Hub 候选"
                      className="rounded-md border border-rose-300/20 p-2 text-rose-100 disabled:opacity-40"
                      disabled={Boolean(busy)}
                      onClick={() => void run(`delete:${candidate.candidate_id}`, () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}`, { method: "DELETE" }))}
                      type="button"
                    >
                      <Trash2 aria-hidden="true" size={16} />
                    </button>
                  </div>
                  {candidate.tools.length ? (
                    <div className="mt-3 rounded-md border border-white/10 bg-white/[0.025] p-3 text-xs text-slate-400">
                      <p>Schema：<span className="font-mono">{candidate.schema_digest.slice(0, 16)}…</span></p>
                      <p className="mt-1">工具：{candidate.tools.map((tool) => tool.name).join("、")}</p>
                    </div>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button className="min-h-9 rounded-md border border-cyan-300/25 px-3 text-sm font-semibold text-cyan-100 disabled:opacity-40" disabled={!status.remote_enabled || Boolean(busy)} onClick={() => void run(`preflight:${candidate.candidate_id}`, () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/preflight`, { method: "POST" }))} type="button">安全预检</button>
                    <button className="min-h-9 rounded-md bg-emerald-300 px-3 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={candidate.state !== "verified" || !candidate.schema_digest || !candidate.activation_eligible || Boolean(busy)} onClick={() => void run(`activate:${candidate.candidate_id}`, () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/activate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_schema_digest: candidate.schema_digest }) }))} type="button">激活</button>
                    <button className="min-h-9 rounded-md border border-white/10 px-3 text-sm font-semibold text-slate-300 disabled:opacity-40" disabled={!candidate.connected || Boolean(busy)} onClick={() => void run(`disconnect:${candidate.candidate_id}`, () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/session`, { method: "DELETE" }))} type="button">断开</button>
                  </div>
                  {candidate.taint_reason ? <p className="mt-2 text-xs text-rose-200">安全状态：{candidate.taint_reason}</p> : null}
                  {!candidate.activation_eligible && candidate.activation_reason ? (
                    <p className="mt-2 text-xs text-amber-100">
                      {activationReasonLabels[candidate.activation_reason] || "该候选当前不可激活。"}
                    </p>
                  ) : null}
                </article>
              ))}
              {candidates.length === 0 ? <p className="py-6 text-sm text-slate-400">尚未添加 Hub 候选。</p> : null}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
