import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, KeyRound, RefreshCw, Search, Shield, Trash2 } from "lucide-react";
import McpHubReviewWorkbench, {
  type HubReviewSelection,
  type HubReviewStatus,
} from "./McpHubReviewWorkbench";
import McpHubTrustedChannel from "./McpHubTrustedChannel";

type Eligibility =
  | "eligible"
  | "static_token_candidate"
  | "oauth_discovery_candidate"
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
  auth_policy?: {
    mode: "static_bearer" | "static_header";
    slot: string;
    header_name: string;
    policy_fingerprint: string;
  };
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
  auth_required?: boolean;
  auth_mode?: "static_bearer" | "static_header" | "";
  auth_header_name?: string;
  auth_slot?: string;
  auth_policy_fingerprint?: string;
  source_digest: string;
  oauth_discovery_available?: boolean;
  registry_eligibility?: Eligibility | "";
}

interface RemoteAuthStatus {
  enabled: boolean;
  static_token_enabled: boolean;
  single_owner_acknowledged: boolean;
  subject_mode: string;
  external_master_key_available: boolean;
  external_master_key_enforced: boolean;
  storage_ready: boolean;
  multi_tenant: false;
}

interface CandidateAuthSummary {
  required: boolean;
  mode?: "static_bearer" | "static_header";
  slot?: string;
  header_name?: string;
  origin?: string;
  policy_fingerprint?: string;
  single_owner_warning?: boolean;
  binding: null | {
    binding_id: string;
    revision: number;
    status: string;
    masked_value: string;
    display_name: string;
  };
}

interface CandidateAuthInput {
  displayName: string;
  secret: string;
  rotateSecret: string;
}

interface RemoteOAuthStatus {
  enabled: boolean;
  dynamic_registration_enabled: boolean;
  remote_auth_enabled: boolean;
  single_owner_acknowledged: boolean;
  external_master_key_available: boolean;
  external_master_key_enforced: boolean;
  storage_ready: boolean;
  client_metadata_document_configured: boolean;
  supported_registration_modes: Array<"pre_registered" | "client_id_metadata_document" | "dynamic">;
  authorization_enabled: false;
  token_storage_enabled: false;
  multi_tenant: false;
}

interface CandidateOAuthSummary {
  discovery: null | {
    discovery_id: string;
    status: string;
    discovery_fingerprint: string;
    resource_uri: string;
    protected_resource_metadata_url: string;
    issuer: string;
    authorization_endpoint: string;
    token_endpoint_origin: string;
    registration_endpoint_available: boolean;
    registration_endpoint: string;
    revocation_endpoint_available: boolean;
    pkce_method: "S256";
    scopes_supported: string[];
    policy_fingerprint: string;
  };
  registration: null | {
    registration_id: string;
    mode: "pre_registered" | "client_id_metadata_document" | "dynamic";
    client_id: string;
    revision: number;
    status: string;
    discovery_fingerprint: string;
  };
  authorization_enabled: false;
  token_storage_enabled: false;
  local_single_owner_warning: boolean;
}

const activationReasonLabels: Record<string, string> = {
  hub_contract_unreviewed: "该候选尚未完成 ModelMirror 执行契约复核，只可预检。",
  hub_preflight_required: "请先完成安全预检。",
  hub_reviewed_contract_drift: "远程 Schema 与已复核契约不一致，禁止激活。",
  hub_contract_collision: "同一 Hub 身份存在不同执行契约，已关闭激活。",
  hub_contract_revoked: "该执行契约已由本地运维者撤销。",
  hub_contract_source_drift: "Registry 来源摘要与冻结契约不一致，需要重新复核。",
  hub_source_drift: "Registry 版本或远程端点已变化，需要重新复核。",
  hub_trusted_revalidation_required: "可信契约需要重新完成实时隔离检查。",
  hub_trusted_environment_blocked: "当前本机 DNS 或隔离出口阻断了远程检查。",
  hub_trusted_degraded: "远程服务当前不可达，请稍后重新检查。",
  mcp_remote_auth_disabled: "远程认证功能尚未启用。",
  mcp_remote_auth_master_key_required: "外部凭据主密钥不可用，已禁止使用认证连接。",
  mcp_remote_auth_single_owner_ack_required: "尚未确认本地单主体运行边界。",
  mcp_remote_auth_binding_missing: "请先绑定该候选的访问 Token。",
  mcp_remote_auth_binding_stale: "认证策略或凭据 revision 已变化，请重新绑定。",
  mcp_remote_auth_scope_denied: "认证绑定不属于当前本地主体。",
};

const safetyReasonLabels: Record<string, string> = {
  hub_dns_private_or_synthetic_denied: "目标解析到了私网或合成地址，隔离出口已拒绝连接。",
  hub_dns_rebinding_denied: "目标地址在连接期间发生变化，隔离出口已拒绝连接。",
  hub_upstream_auth_required: "远程服务要求认证，不符合本轮匿名连接范围。",
  hub_upstream_rate_limited: "远程服务当前限流，请稍后再进行新的预检。",
  hub_upstream_timeout: "远程服务未在限制时间内响应。",
  hub_upstream_redirect_denied: "远程服务发生重定向，不符合固定 Origin 门禁。",
  hub_schema_drift: "远程工具 Schema 已变化，需要重新复核。",
  mcp_remote_auth_unauthorized: "远程服务拒绝了当前凭据（401），请轮换 Token 后重新预检。",
  mcp_remote_auth_forbidden: "当前凭据没有访问该远程服务的权限（403）。",
  mcp_remote_oauth_registration_unknown_outcome: "动态登记请求可能已被授权服务器接收，当前 revision 已封锁，禁止重试旧操作。",
  mcp_remote_oauth_registration_replay_denied: "该发现 revision 已尝试过动态登记；为避免重复创建客户端，必须重新发现后再操作。",
  mcp_remote_oauth_discovery_stale: "OAuth 元数据或 Registry 来源已经漂移，请重新执行发现。",
  mcp_remote_oauth_client_metadata_invalid: "Client ID Metadata Document 不完整或与本机固定回调不一致。",
  mcp_remote_oauth_client_metadata_unsupported: "授权服务器未明确声明支持 Client ID Metadata Document。",
};

function describeSafetyReason(code: string): string {
  return safetyReasonLabels[code] || "隔离预检未通过，该候选不会被激活。";
}

const eligibilityLabels: Record<Eligibility, string> = {
  eligible: "可试连",
  static_token_candidate: "静态 Token 可复核",
  oauth_discovery_candidate: "OAuth 元数据可发现",
  auth_required: "需 OAuth / 动态认证",
  local_runtime: "本地运行时",
  legacy_transport: "旧传输",
  removed: "已下架",
  no_remote: "无可用远程端点",
};

interface CandidateErrorInfo {
  message: string;
  code: string;
  source: "operation" | "summary";
}

class HubRequestError extends Error {
  readonly code: string;

  constructor(message: string, code = "hub_request_failed") {
    super(message);
    this.name = "HubRequestError";
    this.code = code;
  }
}

function candidateErrorInfo(
  reason: unknown,
  source: CandidateErrorInfo["source"],
): CandidateErrorInfo {
  return {
    message: reason instanceof Error ? reason.message : "MCP Hub 操作失败",
    code: reason instanceof HubRequestError ? reason.code : "hub_request_failed",
    source,
  };
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: { code?: string; error?: string; message?: string } | string;
  } & T;
  if (!response.ok) {
    const detail = payload.detail;
    throw new HubRequestError(
      typeof detail === "string"
        ? detail
        : detail?.message || detail?.error || "MCP Hub 请求失败",
      typeof detail === "string" ? "hub_request_failed" : detail?.code,
    );
  }
  return payload;
}

export default function McpHubPanel() {
  const [status, setStatus] = useState<HubStatus | null>(null);
  const [remoteAuthStatus, setRemoteAuthStatus] = useState<RemoteAuthStatus | null>(null);
  const [remoteOAuthStatus, setRemoteOAuthStatus] = useState<RemoteOAuthStatus | null>(null);
  const [candidateAuth, setCandidateAuth] = useState<Record<string, CandidateAuthSummary>>({});
  const [candidateOAuth, setCandidateOAuth] = useState<Record<string, CandidateOAuthSummary>>({});
  const [oauthClientIds, setOauthClientIds] = useState<Record<string, string>>({});
  const [candidateAuthInputs, setCandidateAuthInputs] = useState<Record<string, CandidateAuthInput>>({});
  const [reviewStatus, setReviewStatus] = useState<HubReviewStatus | null>(null);
  const [reviewSelection, setReviewSelection] = useState<HubReviewSelection[]>([]);
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
  const [candidateErrors, setCandidateErrors] = useState<Record<string, CandidateErrorInfo>>({});
  const [trustedRefreshToken, setTrustedRefreshToken] = useState(0);
  const candidateSectionRef = useRef<HTMLElement>(null);

  const refresh = useCallback(async () => {
    setError("");
    try {
      const current = await requestJson<HubStatus>("/api/mcp/hub/status");
      setStatus(current);
      if (!current.enabled) {
        setReviewStatus(null);
        setRemoteAuthStatus(null);
        setRemoteOAuthStatus(null);
        setCandidateAuth({});
        setCandidateOAuth({});
        setServers([]);
        setCandidates([]);
        return;
      }
      const [currentReview, currentRemoteAuth, currentRemoteOAuth] = await Promise.all([
        requestJson<HubReviewStatus>("/api/mcp/hub/reviews/status").catch(() => null),
        requestJson<RemoteAuthStatus>("/api/mcp/remote-auth/status").catch(() => null),
        requestJson<RemoteOAuthStatus>("/api/mcp/remote-auth/oauth/status").catch(() => null),
      ]);
      setReviewStatus(currentReview);
      setRemoteAuthStatus(currentRemoteAuth);
      setRemoteOAuthStatus(currentRemoteOAuth);
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
      const authEntries = await Promise.all(
        candidateData.items
          .filter((candidate) => candidate.auth_required)
          .map(async (candidate) => {
            const auth = await requestJson<CandidateAuthSummary>(
              `/api/mcp/hub/candidates/${candidate.candidate_id}/auth`,
            ).catch(() => null);
            return [candidate.candidate_id, auth] as const;
          }),
      );
      const nextCandidateAuth: Record<string, CandidateAuthSummary> = {};
      for (const [candidateId, auth] of authEntries) {
        if (auth !== null) nextCandidateAuth[candidateId] = auth;
      }
      setCandidateAuth(nextCandidateAuth);
      const oauthEntries = currentRemoteOAuth?.enabled ? await Promise.all(
        candidateData.items
          .filter((candidate) => candidate.oauth_discovery_available)
          .map(async (candidate) => {
            try {
              const oauth = await requestJson<CandidateOAuthSummary>(
                `/api/mcp/hub/candidates/${candidate.candidate_id}/oauth`,
              );
              return { candidateId: candidate.candidate_id, oauth, error: null };
            } catch (reason) {
              return {
                candidateId: candidate.candidate_id,
                oauth: null,
                error: candidateErrorInfo(reason, "summary"),
              };
            }
          }),
      ) : [];
      const nextCandidateOAuth: Record<string, CandidateOAuthSummary> = {};
      for (const entry of oauthEntries) {
        if (entry.oauth !== null) nextCandidateOAuth[entry.candidateId] = entry.oauth;
      }
      setCandidateOAuth(nextCandidateOAuth);
      setCandidateErrors((currentErrors) => {
        const next = { ...currentErrors };
        for (const entry of oauthEntries) {
          if (entry.error) next[entry.candidateId] = entry.error;
          else if (next[entry.candidateId]?.source === "summary") delete next[entry.candidateId];
        }
        return next;
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "MCP Hub 加载失败");
    }
  }, [category, eligibility, page, query]);

  const updateAuthInput = (
    candidateId: string,
    field: keyof CandidateAuthInput,
    value: string,
  ) => {
    setCandidateAuthInputs((current) => ({
      ...current,
      [candidateId]: {
        displayName: current[candidateId]?.displayName || "Hub MCP Token",
        secret: current[candidateId]?.secret || "",
        rotateSecret: current[candidateId]?.rotateSecret || "",
        [field]: value,
      },
    }));
  };

  const clearAuthSecrets = (candidateId: string) => {
    setCandidateAuthInputs((current) => ({
      ...current,
      [candidateId]: {
        displayName: current[candidateId]?.displayName || "Hub MCP Token",
        secret: "",
        rotateSecret: "",
      },
    }));
  };

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const refreshHubViews = useCallback(async () => {
    await refresh();
    setTrustedRefreshToken((current) => current + 1);
  }, [refresh]);

  const run = async <T,>(
    key: string,
    operation: () => Promise<T>,
    onSuccess?: (result: T) => void,
    candidateId?: string,
  ) => {
    setBusy(key);
    setError("");
    setNotice("");
    if (candidateId) {
      setCandidateErrors((current) => {
        const next = { ...current };
        delete next[candidateId];
        return next;
      });
    }
    try {
      const result = await operation();
      await refreshHubViews();
      onSuccess?.(result);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "MCP Hub 操作失败";
      if (candidateId) {
        await refreshHubViews().catch(() => undefined);
        setCandidateErrors((current) => ({
          ...current,
          [candidateId]: candidateErrorInfo(reason, "operation"),
        }));
      } else {
        setError(message);
      }
    } finally {
      setBusy("");
    }
  };

  const deleteCandidate = (candidate: HubCandidate) => {
    const localCredentials = [
      candidate.auth_required ? "本地 Token" : "",
      candidate.oauth_discovery_available ? "本机 OAuth client 登记" : "",
    ].filter(Boolean).join("和");
    const confirmed = window.confirm(
      `删除 ${candidate.server_name}？这会${localCredentials ? `撤销${localCredentials}、` : ""}断开当前会话并从“我的 Hub 连接”移除该候选。`,
    );
    if (!confirmed) return;
    void run(
      `delete:${candidate.candidate_id}`,
      () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}`, { method: "DELETE" }),
      () => setNotice(`${candidate.server_name} 已从“我的 Hub 连接”删除。`),
      candidate.candidate_id,
    );
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

  const remoteAuthOperational = Boolean(
    remoteAuthStatus?.enabled &&
    remoteAuthStatus.static_token_enabled &&
    remoteAuthStatus.single_owner_acknowledged &&
    remoteAuthStatus.external_master_key_available &&
    remoteAuthStatus.external_master_key_enforced &&
    remoteAuthStatus.storage_ready,
  );

  const remoteOAuthOperational = Boolean(
    remoteOAuthStatus?.enabled &&
    remoteOAuthStatus.remote_auth_enabled &&
    remoteOAuthStatus.single_owner_acknowledged &&
    remoteOAuthStatus.external_master_key_available &&
    remoteOAuthStatus.external_master_key_enforced &&
    remoteOAuthStatus.storage_ready,
  );

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
              Registry 收录不代表安全认证。这里只允许固定公网 HTTPS 的 Streamable HTTP 端点；静态 Secret 进入加密槽，OAuth R2A 仅发现标准元数据和登记 public client。用户不能修改 URL、Header 名、环境变量或目标范围。
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
          <span className={`rounded-md border px-2 py-1 ${remoteAuthOperational ? "border-emerald-300/20 text-emerald-100" : "border-amber-300/20 text-amber-100"}`}>
            静态 Token：{remoteAuthOperational ? "本机可用" : "关闭或密钥未就绪"}
          </span>
          <span className={`rounded-md border px-2 py-1 ${remoteOAuthOperational ? "border-violet-300/20 text-violet-100" : "border-amber-300/20 text-amber-100"}`}>
            OAuth 发现：{remoteOAuthOperational ? "本机可用" : "默认关闭或基础未就绪"}
          </span>
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
          <McpHubTrustedChannel
            onChanged={refreshHubViews}
            refreshToken={trustedRefreshToken}
          />

          <section className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
            <div className="mb-4">
              <h3 className="font-semibold text-white">Registry 发现</h3>
              <p className="mt-1 text-sm text-slate-400">浏览官方元数据并选择候选。未经复核的条目只能进入隔离预检，不能直接用于 Runtime。</p>
            </div>
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
                const connectable = server.remotes.find((remote) => (
                  remote.eligibility === "eligible" ||
                  remote.eligibility === "static_token_candidate" ||
                  remote.eligibility === "oauth_discovery_candidate"
                ));
                const matchingCandidate = connectable
                  ? candidates.find((candidate) => (
                    candidate.server_name === server.server_name &&
                    candidate.version === server.version &&
                    candidate.origin === connectable.origin
                  ))
                  : undefined;
                const matchingAuth = matchingCandidate ? candidateAuth[matchingCandidate.candidate_id] : undefined;
                const staticTokenReady = connectable?.eligibility !== "static_token_candidate" || matchingAuth?.binding?.status === "active";
                const reviewKey = connectable ? `${server.server_name}:${server.version}:${connectable.remote_id}` : "";
                const reviewSelected = Boolean(
                  reviewKey && reviewSelection.some((item) => (
                    `${item.server_name}:${item.version}:${item.remote_id}` === reviewKey
                  )),
                );
                const alreadyAdded = Boolean(matchingCandidate);
                return (
                  <article className="rounded-lg border border-white/10 bg-ink-950/45 p-4" key={`${server.server_name}:${server.version}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h4 className="truncate font-semibold text-white">{server.title}</h4>
                        <p className="mt-1 break-all font-mono text-xs text-slate-500">{server.server_name}@{server.version}</p>
                        {server.publisher ? <p className="mt-1 text-xs text-slate-400">Publisher：{server.publisher}</p> : null}
                      </div>
                      <span className={`shrink-0 rounded-md border px-2 py-1 text-xs font-semibold ${server.eligibility === "eligible" ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100" : server.eligibility === "static_token_candidate" ? "border-cyan-300/25 bg-cyan-300/10 text-cyan-100" : server.eligibility === "oauth_discovery_candidate" ? "border-violet-300/25 bg-violet-300/10 text-violet-100" : "border-white/10 text-slate-400"}`}>
                        {eligibilityLabels[server.eligibility]}
                      </span>
                    </div>
                    <p className="mt-3 line-clamp-3 text-sm leading-6 text-slate-400">{server.description || "暂无描述"}</p>
                    {server.categories?.length ? <p className="mt-2 text-xs text-slate-500">分类：{server.categories.join("、")}</p> : null}
                    {connectable ? <p className="mt-2 break-all text-xs text-cyan-100">Origin：{connectable.origin}</p> : null}
                    {connectable?.auth_policy ? (
                      <p className="mt-1 text-xs text-slate-400">
                        认证：{connectable.auth_policy.mode === "static_bearer" ? "Bearer Token" : "固定秘密 Header"} · Header：{connectable.auth_policy.header_name}
                      </p>
                    ) : null}
                    {connectable && connectable.eligibility !== "oauth_discovery_candidate" && reviewStatus?.enabled ? (
                      <label className="mt-3 flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-cyan-300/20 bg-cyan-300/5 px-3 text-xs text-cyan-100">
                        <input
                          checked={reviewSelected}
                          disabled={
                            (!reviewSelected && reviewSelection.length >= reviewStatus.max_batch_size) ||
                            !staticTokenReady
                          }
                          onChange={() => setReviewSelection((current) => {
                            if (reviewSelected) {
                              return current.filter((item) => `${item.server_name}:${item.version}:${item.remote_id}` !== reviewKey);
                            }
                            if (current.length >= reviewStatus.max_batch_size) return current;
                            return [...current, {
                              server_name: server.server_name,
                              version: server.version,
                              remote_id: connectable.remote_id,
                              title: server.title || server.server_name,
                              origin: connectable.origin,
                            }];
                          })}
                          type="checkbox"
                        />
                        {staticTokenReady ? "加入受控复核批次" : "绑定 Token 后可加入复核"}
                      </label>
                    ) : null}
                    <button
                      className="mt-3 min-h-10 rounded-lg bg-hire-300 px-3 py-2 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40"
                      disabled={!connectable || (connectable.eligibility !== "oauth_discovery_candidate" && !status.remote_enabled) || alreadyAdded || Boolean(busy)}
                      onClick={() => connectable && void run(`add:${server.server_name}`, () => requestJson("/api/mcp/hub/candidates", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ server_name: server.server_name, version: server.version, remote_id: connectable.remote_id }),
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

          {reviewStatus?.enabled ? (
            <McpHubReviewWorkbench
              onClearSelection={() => setReviewSelection([])}
              onHubChanged={refreshHubViews}
              selected={reviewSelection}
              status={reviewStatus}
            />
          ) : null}

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
              {candidates.map((candidate) => {
                const revoked = candidate.activation_reason === "hub_contract_revoked";
                const auth = candidateAuth[candidate.candidate_id];
                const authInput = candidateAuthInputs[candidate.candidate_id] || {
                  displayName: "Hub MCP Token",
                  secret: "",
                  rotateSecret: "",
                };
                const activeBinding = auth?.binding?.status === "active" ? auth.binding : null;
                const authReady = !candidate.auth_required || Boolean(activeBinding && remoteAuthOperational);
                const oauth = candidateOAuth[candidate.candidate_id];
                const oauthDiscovery = oauth?.discovery || null;
                const oauthRegistration = oauth?.registration || null;
                const oauthClientId = oauthClientIds[candidate.candidate_id] || "";
                const oauthRuntimeBlocked = candidate.registry_eligibility === "oauth_discovery_candidate";
                const runtimeAuthReady = authReady && !oauthDiscovery && !oauthRuntimeBlocked;
                const candidateError = candidateErrors[candidate.candidate_id];
                return (
                <article className="rounded-lg border border-white/10 bg-ink-950/45 p-4" key={candidate.candidate_id}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <h4 className="font-semibold text-white">{candidate.server_name}</h4>
                        {candidate.state === "active" ? <CheckCircle2 aria-label="已激活" className="text-emerald-200" size={16} /> : null}
                      </div>
                      <p className="mt-1 text-xs text-slate-500">{candidate.version} · {candidate.origin}</p>
                      <p className="mt-2 text-sm text-slate-300">状态：{revoked ? "已撤销" : candidate.state} · {candidate.connected ? "已连接" : "未连接"}</p>
                    </div>
                    <button
                      aria-label={`删除 Hub 候选 ${candidate.server_name}`}
                      className="rounded-md border border-rose-300/20 p-2 text-rose-100 disabled:opacity-40"
                      disabled={Boolean(busy)}
                      onClick={() => deleteCandidate(candidate)}
                      type="button"
                    >
                      <Trash2 aria-hidden="true" size={16} />
                    </button>
                  </div>
                  {candidate.auth_required ? (
                    <section className="mt-3 rounded-lg border border-cyan-300/20 bg-cyan-300/5 p-3" aria-label={`${candidate.server_name} 认证设置`}>
                      <div className="flex items-start gap-2">
                        <KeyRound aria-hidden="true" className="mt-0.5 shrink-0 text-cyan-200" size={16} />
                        <div className="min-w-0 text-xs leading-5 text-slate-300">
                          <p className="font-semibold text-cyan-100">固定静态 Token</p>
                          <p className="break-all">Origin：{auth?.origin || candidate.origin}</p>
                          <p>认证：{auth?.mode === "static_bearer" ? "Bearer Token" : "固定秘密 Header"} · Header：{auth?.header_name || candidate.auth_header_name}</p>
                          <p className="mt-1 text-amber-100">仅适用于本机可信运维者；当前不是多租户隔离能力。</p>
                        </div>
                      </div>
                      {!remoteAuthOperational ? (
                        <p className="mt-3 rounded-md border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-xs text-amber-100">
                          远程认证开关、单主体确认或外部主密钥尚未就绪，凭据操作与预检均已关闭。
                        </p>
                      ) : null}
                      {activeBinding ? (
                        <div className="mt-3 space-y-3">
                          <div className="rounded-md border border-white/10 bg-ink-950/50 px-3 py-2 text-xs text-slate-300">
                            <p>{activeBinding.display_name || "Hub MCP Token"} · {activeBinding.masked_value || "已安全保存"}</p>
                            <p className="mt-1 font-mono text-slate-500">revision {activeBinding.revision}</p>
                          </div>
                          <div className="flex flex-col gap-2 sm:flex-row">
                            <label className="min-w-0 flex-1">
                              <span className="sr-only">轮换 {candidate.server_name} Token</span>
                              <input
                                autoComplete="new-password"
                                className="min-h-10 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 text-sm text-white outline-none focus:border-cyan-300/50"
                                onChange={(event) => updateAuthInput(candidate.candidate_id, "rotateSecret", event.target.value)}
                                placeholder="输入新 Token（保存后不再显示）"
                                type="password"
                                value={authInput.rotateSecret}
                              />
                            </label>
                            <button
                              className="min-h-10 rounded-md border border-cyan-300/25 px-3 text-sm font-semibold text-cyan-100 disabled:opacity-40"
                              disabled={!remoteAuthOperational || !authInput.rotateSecret || Boolean(busy)}
                              onClick={() => void run(
                                `rotate-auth:${candidate.candidate_id}`,
                                () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/auth-bindings/${activeBinding.binding_id}/rotate`, {
                                  method: "POST",
                                  headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify({ secret: authInput.rotateSecret, expected_revision: activeBinding.revision }),
                                }),
                                () => {
                                  clearAuthSecrets(candidate.candidate_id);
                                  setNotice(`${candidate.server_name} 的 Token 已轮换，旧会话已断开。`);
                                },
                                candidate.candidate_id,
                              )}
                              type="button"
                            >
                              轮换 Token
                            </button>
                            <button
                              className="min-h-10 rounded-md border border-rose-300/20 px-3 text-sm font-semibold text-rose-100 disabled:opacity-40"
                              disabled={!remoteAuthOperational || Boolean(busy)}
                              onClick={() => {
                                if (!window.confirm(`撤销 ${candidate.server_name} 的 Token？当前 Hub 会话会立即断开。`)) return;
                                void run(
                                  `revoke-auth:${candidate.candidate_id}`,
                                  () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/auth-bindings/${activeBinding.binding_id}`, { method: "DELETE" }),
                                  () => {
                                    clearAuthSecrets(candidate.candidate_id);
                                    setNotice(`${candidate.server_name} 的 Token 已撤销。`);
                                  },
                                  candidate.candidate_id,
                                );
                              }}
                              type="button"
                            >
                              撤销 Token
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto]">
                          <label>
                            <span className="sr-only">凭据显示名称</span>
                            <input
                              className="min-h-10 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 text-sm text-white outline-none focus:border-cyan-300/50"
                              onChange={(event) => updateAuthInput(candidate.candidate_id, "displayName", event.target.value)}
                              placeholder="凭据名称"
                              type="text"
                              value={authInput.displayName}
                            />
                          </label>
                          <label>
                            <span className="sr-only">绑定 {candidate.server_name} Token</span>
                            <input
                              autoComplete="new-password"
                              className="min-h-10 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 text-sm text-white outline-none focus:border-cyan-300/50"
                              onChange={(event) => updateAuthInput(candidate.candidate_id, "secret", event.target.value)}
                              placeholder="输入 Token（保存后不再显示）"
                              type="password"
                              value={authInput.secret}
                            />
                          </label>
                          <button
                            className="min-h-10 rounded-md bg-cyan-200 px-3 text-sm font-semibold text-ink-950 disabled:bg-slate-700 disabled:text-slate-400"
                            disabled={!remoteAuthOperational || !authInput.displayName.trim() || !authInput.secret || Boolean(busy)}
                            onClick={() => void run(
                              `bind-auth:${candidate.candidate_id}`,
                              () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/auth-bindings`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                  slot: auth?.slot || candidate.auth_slot,
                                  display_name: authInput.displayName.trim(),
                                  secret: authInput.secret,
                                }),
                              }),
                              () => {
                                clearAuthSecrets(candidate.candidate_id);
                                setNotice(`${candidate.server_name} 的 Token 已安全保存。`);
                              },
                              candidate.candidate_id,
                            )}
                            type="button"
                          >
                            保存 Token
                          </button>
                        </div>
                      )}
                    </section>
                  ) : null}
                  {candidate.oauth_discovery_available && !candidate.auth_required ? (
                    <section className="mt-3 rounded-lg border border-violet-300/20 bg-violet-300/5 p-3" aria-label={`${candidate.server_name} OAuth 发现设置`}>
                      <div className="flex items-start gap-2">
                        <Shield aria-hidden="true" className="mt-0.5 shrink-0 text-violet-200" size={16} />
                        <div className="min-w-0 text-xs leading-5 text-slate-300">
                          <p className="font-semibold text-violet-100">OAuth 元数据发现（R2A）</p>
                          <p>只读取 Protected Resource Metadata 与授权服务器元数据，并登记 public client。</p>
                          <p className="mt-1 text-amber-100">本轮不会打开授权页面、接收回调、保存 Token 或让 OAuth 工具进入 Runtime。</p>
                        </div>
                      </div>
                      {!remoteOAuthOperational ? (
                        <p className="mt-3 rounded-md border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-xs text-amber-100">
                          OAuth 发现开关、单主体确认或外部主密钥尚未就绪，发现与登记均已关闭。
                        </p>
                      ) : null}
                      {!oauthDiscovery ? (
                        <button
                          className="mt-3 min-h-10 rounded-md border border-violet-300/25 px-3 text-sm font-semibold text-violet-100 disabled:opacity-40"
                          disabled={!remoteOAuthOperational || Boolean(busy)}
                          onClick={() => void run(
                            `oauth-discover:${candidate.candidate_id}`,
                            () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/oauth/discover`, {
                              method: "POST",
                              headers: { "Content-Type": "application/json" },
                              body: JSON.stringify({ expected_source_digest: candidate.source_digest }),
                            }),
                            () => setNotice(`${candidate.server_name} 的 OAuth 元数据已冻结；尚未执行用户授权。`),
                            candidate.candidate_id,
                          )}
                          type="button"
                        >
                          检查并冻结 OAuth 元数据
                        </button>
                      ) : (
                        <div className="mt-3 space-y-3">
                          <div className="rounded-md border border-white/10 bg-ink-950/50 px-3 py-2 text-xs leading-5 text-slate-300">
                            <p className="break-all">资源：{oauthDiscovery.resource_uri}</p>
                            <p className="break-all">Issuer：{oauthDiscovery.issuer}</p>
                            <p>PKCE：{oauthDiscovery.pkce_method} · Token Origin：{oauthDiscovery.token_endpoint_origin}</p>
                            {oauthDiscovery.registration_endpoint ? <p className="break-all">Registration Endpoint：{oauthDiscovery.registration_endpoint}</p> : null}
                            <p>Scope：{oauthDiscovery.scopes_supported.length ? oauthDiscovery.scopes_supported.join("、") : "未声明（R2B 前仍不可授权）"}</p>
                            <p className="mt-1 break-all font-mono text-slate-500">fingerprint {oauthDiscovery.discovery_fingerprint.slice(0, 20)}…</p>
                          </div>
                          {oauthRegistration ? (
                            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-emerald-300/20 bg-emerald-300/5 px-3 py-2 text-xs text-slate-300">
                              <div className="min-w-0">
                                <p className="font-semibold text-emerald-100">Public client 已登记 · {oauthRegistration.mode}</p>
                                <p className="break-all">Client ID：{oauthRegistration.client_id}</p>
                                <p className="font-mono text-slate-500">revision {oauthRegistration.revision}</p>
                              </div>
                              <button
                                className="min-h-9 rounded-md border border-rose-300/20 px-3 font-semibold text-rose-100 disabled:opacity-40"
                                disabled={!remoteOAuthOperational || Boolean(busy)}
                                onClick={() => {
                                  if (!window.confirm(`撤销 ${candidate.server_name} 的本机 OAuth client 登记？这不会调用远程删除接口。`)) return;
                                  void run(
                                    `oauth-revoke:${candidate.candidate_id}`,
                                    () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/oauth/registrations/${oauthRegistration.registration_id}`, { method: "DELETE" }),
                                    () => setNotice(`${candidate.server_name} 的本机 OAuth client 登记已撤销。`),
                                    candidate.candidate_id,
                                  );
                                }}
                                type="button"
                              >
                                撤销本机登记
                              </button>
                            </div>
                          ) : (
                            <div className="space-y-2">
                              <div className="flex flex-col gap-2 sm:flex-row">
                                <label className="min-w-0 flex-1">
                                  <span className="sr-only">{candidate.server_name} 预登记 public client ID</span>
                                  <input
                                    className="min-h-10 w-full rounded-md border border-white/10 bg-ink-950/70 px-3 text-sm text-white outline-none focus:border-violet-300/50"
                                    onChange={(event) => setOauthClientIds((current) => ({ ...current, [candidate.candidate_id]: event.target.value }))}
                                    placeholder="已有 public client_id（不接受 client secret）"
                                    type="text"
                                    value={oauthClientId}
                                  />
                                </label>
                                <button
                                  className="min-h-10 rounded-md border border-violet-300/25 px-3 text-sm font-semibold text-violet-100 disabled:opacity-40"
                                  disabled={!remoteOAuthOperational || !oauthClientId.trim() || Boolean(busy)}
                                  onClick={() => void run(
                                    `oauth-register:${candidate.candidate_id}`,
                                    () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/oauth/registrations`, {
                                      method: "POST",
                                      headers: { "Content-Type": "application/json" },
                                      body: JSON.stringify({
                                        expected_discovery_fingerprint: oauthDiscovery.discovery_fingerprint,
                                        mode: "pre_registered",
                                        client_id: oauthClientId.trim(),
                                      }),
                                    }),
                                    () => {
                                      setOauthClientIds((current) => ({ ...current, [candidate.candidate_id]: "" }));
                                      setNotice(`${candidate.server_name} 的 public client 已在本机登记；尚未授权。`);
                                    },
                                    candidate.candidate_id,
                                  )}
                                  type="button"
                                >
                                  登记已有 Client ID
                                </button>
                              </div>
                              {remoteOAuthStatus?.supported_registration_modes.includes("client_id_metadata_document") ? (
                                <button
                                  className="min-h-9 rounded-md border border-white/10 px-3 text-xs font-semibold text-slate-200 disabled:opacity-40"
                                  disabled={!remoteOAuthOperational || Boolean(busy)}
                                  onClick={() => void run(
                                    `oauth-client-metadata:${candidate.candidate_id}`,
                                    () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/oauth/registrations`, {
                                      method: "POST",
                                      headers: { "Content-Type": "application/json" },
                                      body: JSON.stringify({
                                        expected_discovery_fingerprint: oauthDiscovery.discovery_fingerprint,
                                        mode: "client_id_metadata_document",
                                      }),
                                    }),
                                    () => setNotice(`${candidate.server_name} 已绑定部署配置中的 Client ID Metadata Document。`),
                                    candidate.candidate_id,
                                  )}
                                  type="button"
                                >
                                  使用部署配置的 Client ID Metadata Document
                                </button>
                              ) : null}
                              {remoteOAuthStatus?.dynamic_registration_enabled && oauthDiscovery.registration_endpoint_available ? (
                                <button
                                  className="ml-2 min-h-9 rounded-md border border-amber-300/20 px-3 text-xs font-semibold text-amber-100 disabled:opacity-40"
                                  disabled={!remoteOAuthOperational || Boolean(busy)}
                                  onClick={() => {
                                    if (!window.confirm(`动态客户端登记会向 ${oauthDiscovery.registration_endpoint} 执行一次不可自动重试的写操作。继续？`)) return;
                                    void run(
                                      `oauth-dcr:${candidate.candidate_id}`,
                                      () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/oauth/registrations`, {
                                        method: "POST",
                                        headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({
                                          expected_discovery_fingerprint: oauthDiscovery.discovery_fingerprint,
                                          mode: "dynamic",
                                        }),
                                      }),
                                      () => setNotice(`${candidate.server_name} 已完成一次 public client 动态登记；尚未授权。`),
                                      candidate.candidate_id,
                                    );
                                  }}
                                  type="button"
                                >
                                  动态登记 Public Client
                                </button>
                              ) : null}
                            </div>
                          )}
                        </div>
                      )}
                    </section>
                  ) : null}
                  {candidate.tools.length && !revoked ? (
                    <div className="mt-3 rounded-md border border-white/10 bg-white/[0.025] p-3 text-xs text-slate-400">
                      <p>Schema：<span className="font-mono">{candidate.schema_digest.slice(0, 16)}…</span></p>
                      <p className="mt-1">工具：{candidate.tools.map((tool) => tool.name).join("、")}</p>
                    </div>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button className="min-h-9 rounded-md border border-cyan-300/25 px-3 text-sm font-semibold text-cyan-100 disabled:cursor-not-allowed disabled:opacity-40" disabled={!status.remote_enabled || !runtimeAuthReady || revoked || Boolean(busy)} onClick={() => void run(`preflight:${candidate.candidate_id}`, () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/preflight`, { method: "POST" }), undefined, candidate.candidate_id)} type="button">安全预检</button>
                    <button className="min-h-9 rounded-md bg-emerald-300 px-3 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400" disabled={candidate.state !== "verified" || !candidate.schema_digest || !candidate.activation_eligible || !runtimeAuthReady || Boolean(busy)} onClick={() => void run(`activate:${candidate.candidate_id}`, () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/activate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_schema_digest: candidate.schema_digest }) }), undefined, candidate.candidate_id)} type="button">激活</button>
                    <button className="min-h-9 rounded-md border border-white/10 px-3 text-sm font-semibold text-slate-300 disabled:cursor-not-allowed disabled:opacity-40" disabled={!candidate.connected || Boolean(busy)} onClick={() => void run(`disconnect:${candidate.candidate_id}`, () => requestJson(`/api/mcp/hub/candidates/${candidate.candidate_id}/session`, { method: "DELETE" }), undefined, candidate.candidate_id)} type="button">断开</button>
                  </div>
                  {candidateError ? (
                    <div className="mt-3 flex items-start gap-2 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100" role="alert">
                      <AlertTriangle aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
                      <div className="min-w-0">
                        <p>本次操作未完成：{candidateError.message}</p>
                        <p className="mt-1 text-xs leading-5 text-rose-100/90">
                          安全状态：{safetyReasonLabels[candidateError.code] || activationReasonLabels[candidateError.code] || "请求已安全停止，可根据错误码核对配置后发起一次新操作。"}
                        </p>
                        <p className="break-all font-mono text-xs text-slate-400">错误码：{candidateError.code}</p>
                      </div>
                    </div>
                  ) : null}
                  {candidate.taint_reason && candidateError?.code !== candidate.taint_reason ? (
                    <div className="mt-2 text-xs leading-5 text-rose-200">
                      <p>安全状态：{describeSafetyReason(candidate.taint_reason)}</p>
                      <p className="font-mono text-slate-500">错误码：{candidate.taint_reason}</p>
                    </div>
                  ) : null}
                  {!candidate.activation_eligible && candidate.activation_reason ? (
                    <p className="mt-2 text-xs text-amber-100">
                      {activationReasonLabels[candidate.activation_reason] || "该候选当前不可激活。"}
                    </p>
                  ) : null}
                </article>
                );
              })}
              {candidates.length === 0 ? <p className="py-6 text-sm text-slate-400">尚未添加 Hub 候选。</p> : null}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
