import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, ExternalLink, RefreshCw, ShieldCheck } from "lucide-react";

interface OAuthSummary {
  discovery: null | {
    discovery_fingerprint: string;
    issuer: string;
    scope_source: string;
    recommended_scopes: string[];
    recommended_scope_digest: string;
    offline_access_available: boolean;
  };
  registration: null | {
    registration_digest: string;
    mode: string;
    status: string;
  };
  authorization_session: null | {
    status: string;
    scopes: string[];
  };
  token: null | {
    token_id: string;
    revision: number;
    status: string;
    scopes: string[];
    refresh_available: boolean;
    expires_at: number | null;
  };
  scope_assessment?: {
    classification: "dangerous" | "unknown" | "read_candidate";
    dangerous_scopes: string[];
    unknown_scopes: string[];
    read_candidate_scopes: string[];
  };
}

interface RemoteSummary {
  project_id: string;
  origin: string;
  version: string;
  protocol_version: string;
  auth_mode: "static_bearer" | "static_header" | "oauth_authorization_code_pkce";
  target_state: {
    state: string;
    reason_code: string;
  };
  oauth: OAuthSummary | null;
  reviewed_contract: null | {
    contract_id: string;
    contract_fingerprint: string;
  };
  contract_error: string;
  activation_eligible: false;
  runtime_tool_count: 0;
  credential_binding_ready: boolean;
  catalog_oauth_enabled: boolean;
}

interface ReviewProposal {
  proposal_id: string;
  proposal_digest: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}

interface ReviewItem {
  item_id: string;
  state: string;
  error_code: string;
  evidence_digest: string;
  contract_fingerprint: string;
  proposal: ReviewProposal | null;
  evidence: null | {
    effect_proposals: Record<string, string>;
    schema_digest: string;
    cleanup: Record<string, boolean>;
  };
}

interface ReviewRun {
  run_id: string;
  status: string;
  error_code: string;
  items: ReviewItem[];
}

class RemoteRequestError extends Error {
  readonly code: string;

  constructor(message: string, code = "mcp_remote_request_failed") {
    super(message);
    this.code = code;
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = (await response.json().catch(() => ({}))) as T & {
    detail?: { code?: string; error?: string; message?: string } | string;
  };
  if (!response.ok) {
    const detail = payload.detail;
    throw new RemoteRequestError(
      typeof detail === "string"
        ? detail
        : detail?.error || detail?.message || "远程 MCP 复核请求失败",
      typeof detail === "string" ? undefined : detail?.code,
    );
  }
  return payload;
}

const stateLabels: Record<string, string> = {
  draft: "未复核",
  reviewing: "复核中",
  reviewed: "已复核",
  active: "已激活",
  drifted: "已漂移",
  tainted: "结果未知，已封锁",
  disconnected: "已断开",
  revoked: "已撤销",
};

const reviewErrorMessages: Record<string, string> = {
  hub_dns_private_or_synthetic_denied:
    "目标解析到了私网或合成地址，隔离出口已拒绝连接。",
  hub_non_tool_capability_denied:
    "远程服务声明了当前 tools-only 契约不允许的额外能力，已安全阻断；这不是凭据错误。",
  hub_upstream_auth_required:
    "远程服务要求认证；Review Factory 将只使用本地加密槽中已绑定的固定凭据继续复核。",
};

function authLabel(mode: RemoteSummary["auth_mode"]): string {
  if (mode === "oauth_authorization_code_pkce") return "OAuth 2.1 + PKCE";
  if (mode === "static_header") return "固定秘密 Header";
  return "固定 Bearer Token";
}

export default function McpCatalogRemotePanel({ projectId }: { projectId: string }) {
  const [summary, setSummary] = useState<RemoteSummary | null>(null);
  const [run, setRun] = useState<ReviewRun | null>(null);
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [authorizationUrl, setAuthorizationUrl] = useState("");
  const [requestRefreshToken, setRequestRefreshToken] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<{ message: string; code: string } | null>(null);

  const item = run?.items[0] ?? null;
  const readCandidates = useMemo(
    () =>
      Object.entries(item?.evidence?.effect_proposals ?? {})
        .filter(([, effect]) => effect === "read_candidate")
        .map(([name]) => name)
        .sort(),
    [item?.evidence?.effect_proposals],
  );

  const loadSummary = async () => {
    const next = await requestJson<RemoteSummary>(`/api/mcp/catalog/${projectId}/remote`);
    setSummary(next);
    return next;
  };

  const loadRun = async (runId = run?.run_id) => {
    if (!runId) return null;
    const next = await requestJson<ReviewRun>(`/api/mcp/remote/review-runs/${runId}`);
    setRun(next);
    return next;
  };

  useEffect(() => {
    let active = true;
    void loadSummary().catch((reason: unknown) => {
      if (!active) return;
      setError({
        message: reason instanceof Error ? reason.message : "远程复核状态加载失败",
        code: reason instanceof RemoteRequestError ? reason.code : "mcp_remote_request_failed",
      });
    });
    return () => {
      active = false;
    };
    // projectId changes define a new panel instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return undefined;
    const timer = window.setInterval(() => {
      void loadRun(run.run_id).catch(() => undefined);
    }, 1200);
    return () => window.clearInterval(timer);
    // Poll only the current run identity and lifecycle status.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.run_id, run?.status]);

  useEffect(() => {
    if (item?.state === "awaiting_decision") {
      setSelectedTools(readCandidates);
    }
  }, [item?.state, readCandidates]);

  useEffect(() => {
    if (summary?.oauth?.token) {
      setAuthorizationUrl("");
    }
  }, [summary?.oauth?.token]);

  const perform = async (key: string, action: () => Promise<unknown>, message: string) => {
    setBusy(key);
    setError(null);
    setNotice("");
    try {
      await action();
      await Promise.all([loadSummary(), loadRun()]);
      setNotice(message);
    } catch (reason) {
      setError({
        message: reason instanceof Error ? reason.message : "远程 MCP 操作失败",
        code: reason instanceof RemoteRequestError ? reason.code : "mcp_remote_request_failed",
      });
    } finally {
      setBusy("");
    }
  };

  const oauth = summary?.oauth;
  const oauthDiscovery = oauth?.discovery;
  const oauthRegistration = oauth?.registration;
  const oauthToken = oauth?.token;
  const oauthDangerousScopes = oauth?.scope_assessment?.dangerous_scopes ?? [];
  const oauthHighRisk = oauthDangerousScopes.length > 0;

  return (
    <section
      aria-label="认证型远程 MCP 复核"
      className="relative mt-3 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.045] p-3 text-xs text-slate-300"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold text-cyan-100">
            <ShieldCheck aria-hidden="true" size={16} />
            认证型远程 MCP
          </h3>
          <p className="mt-1 leading-5 text-slate-400">
            本地单主体运维功能。Origin、认证策略与工具 Schema 由服务端冻结。
          </p>
        </div>
        <button
          aria-label="刷新远程复核状态"
          className="inline-flex min-h-9 items-center gap-2 rounded-md border border-white/10 px-3 font-semibold text-slate-200 transition hover:border-cyan-300/30 hover:text-cyan-100 disabled:opacity-40"
          disabled={Boolean(busy)}
          onClick={() => void perform("refresh", loadSummary, "远程复核状态已刷新。")}
          type="button"
        >
          <RefreshCw aria-hidden="true" size={14} />
          刷新
        </button>
      </div>

      {summary ? (
        <dl className="mt-3 grid gap-x-4 gap-y-2 border-y border-white/10 py-3 sm:grid-cols-2">
          <div>
            <dt className="text-slate-500">固定 Origin</dt>
            <dd className="mt-1 break-all font-mono text-slate-200">{summary.origin}</dd>
          </div>
          <div>
            <dt className="text-slate-500">认证与协议</dt>
            <dd className="mt-1 text-slate-200">
              {authLabel(summary.auth_mode)} · {summary.protocol_version}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">复核状态</dt>
            <dd className="mt-1 font-semibold text-cyan-100">
              {stateLabels[summary.target_state.state] || summary.target_state.state}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">运行边界</dt>
            <dd className="mt-1 text-amber-100">R4A 不激活，Runtime 工具数为 0</dd>
          </div>
        </dl>
      ) : (
        <div className="mt-3 h-20 animate-pulse rounded-md bg-white/[0.045]" aria-label="正在加载远程复核状态" />
      )}

      {summary?.auth_mode === "oauth_authorization_code_pkce" && summary.catalog_oauth_enabled ? (
        <div className="mt-3 space-y-3 border-b border-white/10 pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-semibold text-violet-100">OAuth 授权</p>
            <div className="flex flex-wrap gap-2">
              <button
                className="min-h-9 rounded-md border border-violet-300/25 px-3 font-semibold text-violet-100 disabled:opacity-40"
                disabled={Boolean(busy)}
                onClick={() => void perform(
                  "discover",
                  () => requestJson(`/api/mcp/catalog/${projectId}/remote/oauth/discover`, { method: "POST" }),
                  "OAuth 元数据已重新发现。",
                )}
                type="button"
              >
                重新发现
              </button>
              {oauthDiscovery && !oauthRegistration ? (
                <button
                  className="min-h-9 rounded-md border border-violet-300/25 px-3 font-semibold text-violet-100 disabled:opacity-40"
                  disabled={Boolean(busy) || oauthHighRisk}
                  onClick={() => void perform(
                    "register",
                    () => requestJson(`/api/mcp/catalog/${projectId}/remote/oauth/register`, {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                        expected_discovery_fingerprint: oauthDiscovery.discovery_fingerprint,
                      }),
                    }),
                    "OAuth client 已按 manifest 策略登记。",
                  )}
                  type="button"
                >
                  登记 client
                </button>
              ) : null}
            </div>
          </div>
          {oauthDiscovery ? (
            <div className="space-y-2 text-slate-400">
              <p>Issuer：<span className="break-all font-mono text-slate-300">{oauthDiscovery.issuer}</span></p>
              <p>
                固定 Scope：{oauthDiscovery.recommended_scopes.length
                  ? oauthDiscovery.recommended_scopes.join("、")
                  : "省略 scope 参数"}
              </p>
              {oauthHighRisk ? (
                <p className="rounded-md border border-rose-300/25 bg-rose-300/[0.07] p-2.5 text-rose-100" role="alert">
                  固定 Scope 包含高危写入或控制语义（{oauthDangerousScopes.join("、")}）。
                  R4A 不会登记 client、创建授权会话或发布该候选。
                </p>
              ) : null}
              {oauthDiscovery.offline_access_available ? (
                <label className="flex items-start gap-2 text-amber-100">
                  <input
                    checked={requestRefreshToken}
                    onChange={(event) => setRequestRefreshToken(event.target.checked)}
                    type="checkbox"
                  />
                  <span>请求 offline_access，仅用于显式手动刷新，不会后台刷新。</span>
                </label>
              ) : null}
            </div>
          ) : null}
          {oauthDiscovery && oauthRegistration && !oauthToken ? (
            <button
              className="min-h-9 rounded-md bg-violet-300 px-3 font-semibold text-ink-950 disabled:bg-slate-700 disabled:text-slate-400"
              disabled={Boolean(busy) || oauthHighRisk}
              onClick={() => void perform(
                "authorize",
                async () => {
                  const result = await requestJson<{ authorization_url: string }>(
                    `/api/mcp/catalog/${projectId}/remote/oauth/authorize`,
                    {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                        expected_discovery_fingerprint: oauthDiscovery.discovery_fingerprint,
                        expected_registration_digest: oauthRegistration.registration_digest,
                        expected_scope_digest: oauthDiscovery.recommended_scope_digest,
                        request_refresh_token: requestRefreshToken,
                      }),
                    },
                  );
                  setAuthorizationUrl(result.authorization_url);
                },
                "一次性授权链接已创建。",
              )}
              type="button"
            >
              创建授权链接
            </button>
          ) : null}
          {authorizationUrl && !oauthToken ? (
            <a
              className="inline-flex min-h-9 items-center gap-2 rounded-md border border-violet-300/25 px-3 font-semibold text-violet-100"
              href={authorizationUrl}
              rel="noreferrer noopener"
              target="_blank"
            >
              打开授权页面 <ExternalLink aria-hidden="true" size={14} />
            </a>
          ) : null}
          {oauthToken ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-emerald-300/20 bg-emerald-300/[0.045] p-2.5">
              <p className="text-emerald-100">
                Token 已加密保存 · revision {oauthToken.revision} · {oauthToken.scopes.join("、") || "无显式 Scope"}
              </p>
              <div className="flex gap-2">
                {oauthToken.refresh_available ? (
                  <button
                    className="min-h-9 rounded-md border border-emerald-300/25 px-3 font-semibold text-emerald-100 disabled:opacity-40"
                    disabled={Boolean(busy)}
                    onClick={() => void perform(
                      "refresh-token",
                      () => requestJson(
                        `/api/mcp/catalog/${projectId}/remote/oauth/tokens/${oauthToken.token_id}/refresh`,
                        {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ expected_revision: oauthToken.revision }),
                        },
                      ),
                      "Token 已手动刷新；旧 evidence 已失效。",
                    )}
                    type="button"
                  >
                    手动刷新
                  </button>
                ) : null}
                <button
                  className="min-h-9 rounded-md border border-rose-300/25 px-3 font-semibold text-rose-100 disabled:opacity-40"
                  disabled={Boolean(busy)}
                  onClick={() => void perform(
                    "revoke-token",
                    () => requestJson(
                      `/api/mcp/catalog/${projectId}/remote/oauth/tokens/${oauthToken.token_id}`,
                      { method: "DELETE" },
                    ),
                    "本地 Token 已撤销，相关复核状态已失效。",
                  )}
                  type="button"
                >
                  撤销 Token
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : summary?.auth_mode === "oauth_authorization_code_pkce" ? (
        <p className="mt-3 border-b border-amber-300/20 pb-3 leading-5 text-amber-100">
          Catalog OAuth 当前未启用。未开启专用 OAuth 门禁前，不会创建授权会话或保存 Token。
        </p>
      ) : summary?.credential_binding_ready ? (
        <p className="mt-3 border-b border-white/10 pb-3 leading-5 text-slate-400">
          固定凭据仅由本卡片的“服务凭据”区域绑定；明文只进入本地加密槽，
          Review Factory 不接受 Token、Header 或其他客户端字段。若已保存，可直接开始复核。
        </p>
      ) : summary ? (
        <p className="mt-3 border-b border-amber-300/20 pb-3 leading-5 text-amber-100">
          固定凭据槽尚未开放。请先启用 Remote Auth 与 Static Token、本地单主体确认，
          并使用外部主密钥强制策略；未满足前不会保存 Secret。
        </p>
      ) : null}

      <div className="mt-3 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-semibold text-cyan-100">统一 Review Factory</p>
          {!run ? (
            <button
              className="min-h-9 rounded-md border border-cyan-300/25 px-3 font-semibold text-cyan-100 disabled:opacity-40"
              disabled={Boolean(busy) || oauthHighRisk}
              onClick={() => void perform(
                "create-review",
                async () => {
                  const created = await requestJson<ReviewRun>("/api/mcp/remote/review-runs", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      items: [{ target_type: "catalog_project", target_id: projectId }],
                    }),
                  });
                  setRun(created);
                },
                "复核批次已创建。",
              )}
              type="button"
            >
              开始复核
            </button>
          ) : (
            <span className="font-mono text-slate-400">{run.status}</span>
          )}
        </div>

        {item ? (
          <div className="space-y-2 rounded-md border border-white/10 bg-black/10 p-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p>阶段：<span className="font-semibold text-slate-100">{item.state}</span></p>
              {item.evidence?.schema_digest ? (
                <p className="font-mono text-slate-500">Schema {item.evidence.schema_digest.slice(0, 12)}…</p>
              ) : null}
            </div>
            {item.state === "awaiting_call_approval" && item.proposal ? (
              <div className="space-y-2">
                <p>
                  固定代表调用：<span className="font-mono text-cyan-100">{item.proposal.tool_name}</span>
                  （参数由服务端生成）
                </p>
                <button
                  className="min-h-9 rounded-md border border-amber-300/25 px-3 font-semibold text-amber-100 disabled:opacity-40"
                  disabled={Boolean(busy)}
                  onClick={() => void perform(
                    "approve-call",
                    () => requestJson(
                      `/api/mcp/remote/review-runs/${run?.run_id}/items/${item.item_id}/call-proposals/${item.proposal?.proposal_id}/approve`,
                      {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                          expected_proposal_digest: item.proposal?.proposal_digest,
                        }),
                      },
                    ),
                    "代表读取已执行一次，不会自动重试。",
                  )}
                  type="button"
                >
                  批准一次代表读取
                </button>
              </div>
            ) : null}
            {item.state === "awaiting_decision" ? (
              <div className="space-y-2">
                <p className="font-semibold text-slate-100">确认进入契约的只读工具</p>
                {readCandidates.map((tool) => (
                  <label className="flex items-center gap-2" key={tool}>
                    <input
                      checked={selectedTools.includes(tool)}
                      onChange={(event) => setSelectedTools((current) =>
                        event.target.checked
                          ? [...new Set([...current, tool])]
                          : current.filter((name) => name !== tool))}
                      type="checkbox"
                    />
                    <span className="font-mono text-slate-200">{tool}</span>
                  </label>
                ))}
                <div className="flex flex-wrap gap-2">
                  <button
                    className="min-h-9 rounded-md bg-cyan-300 px-3 font-semibold text-ink-950 disabled:bg-slate-700 disabled:text-slate-400"
                    disabled={!selectedTools.length || Boolean(busy)}
                    onClick={() => void perform(
                      "approve-contract",
                      () => requestJson(
                        `/api/mcp/remote/review-runs/${run?.run_id}/items/${item.item_id}/decision`,
                        {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({
                            decision: "approve",
                            expected_evidence_digest: item.evidence_digest,
                            allowed_tools: selectedTools,
                            tool_effects: Object.fromEntries(selectedTools.map((tool) => [tool, "read"])),
                          }),
                        },
                      ),
                      "只读工具子集已冻结为契约草案。",
                    )}
                    type="button"
                  >
                    批准只读契约草案
                  </button>
                  <button
                    className="min-h-9 rounded-md border border-rose-300/25 px-3 font-semibold text-rose-100 disabled:opacity-40"
                    disabled={Boolean(busy)}
                    onClick={() => void perform(
                      "block-contract",
                      () => requestJson(
                        `/api/mcp/remote/review-runs/${run?.run_id}/items/${item.item_id}/decision`,
                        {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({
                            decision: "block",
                            expected_evidence_digest: item.evidence_digest,
                          }),
                        },
                      ),
                      "该复核项已由本地运维者阻断。",
                    )}
                    type="button"
                  >
                    阻断
                  </button>
                </div>
              </div>
            ) : null}
            {item.state === "approved" ? (
              <button
                className="min-h-9 rounded-md border border-emerald-300/25 px-3 font-semibold text-emerald-100 disabled:opacity-40"
                disabled={Boolean(busy)}
                onClick={() => void perform(
                  "publish-contract",
                  () => requestJson(
                    `/api/mcp/remote/review-runs/${run?.run_id}/items/${item.item_id}/publish`,
                    {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                        expected_contract_fingerprint: item.contract_fingerprint,
                      }),
                    },
                  ),
                  "本机只读契约已发布；R4A 仍不会激活 Runtime。",
                )}
                type="button"
              >
                发布本机契约
              </button>
            ) : null}
            {item.state === "published" && run ? (
              <a
                className="inline-flex min-h-9 items-center gap-2 rounded-md border border-white/10 px-3 font-semibold text-slate-200"
                href={`/api/mcp/remote/review-runs/${run.run_id}/items/${item.item_id}/contract-export`}
              >
                导出确定性契约 <ExternalLink aria-hidden="true" size={14} />
              </a>
            ) : null}
            {item.error_code ? (
              <div className="space-y-1 text-rose-100">
                <p>错误码：<span className="font-mono">{item.error_code}</span></p>
                {reviewErrorMessages[item.error_code] ? (
                  <p>{reviewErrorMessages[item.error_code]}</p>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      {notice ? (
        <p className="mt-3 flex items-start gap-2 text-emerald-100" role="status">
          <CheckCircle2 aria-hidden="true" className="mt-0.5 shrink-0" size={15} />
          {notice}
        </p>
      ) : null}
      {error ? (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-rose-300/25 bg-rose-300/[0.07] p-2.5 text-rose-100" role="alert">
          <AlertTriangle aria-hidden="true" className="mt-0.5 shrink-0" size={15} />
          <div>
            <p>{error.message}</p>
            <p className="mt-1 font-mono text-rose-100/80">{error.code}</p>
          </div>
        </div>
      ) : null}
    </section>
  );
}
