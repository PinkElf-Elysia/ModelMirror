import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Download,
  PauseCircle,
  Play,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

export interface HubReviewStatus {
  enabled: boolean;
  local_publish_enabled: boolean;
  oauth_review_enabled: boolean;
  signing_key_configured: boolean;
  sop_version: string;
  max_batch_size: number;
  max_concurrency: number;
  active_run_id: string | null;
  operator_scope: string;
  multi_tenant_admin: boolean;
}

export interface HubReviewSelection {
  server_name: string;
  version: string;
  remote_id: string;
  title: string;
  origin: string;
}

interface ReviewEvent {
  event_id: string;
  stage: string;
  status: string;
  safe_to_retry: boolean;
  error_code: string;
}

interface ReviewProposal {
  proposal_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  schema_digest: string;
  proposal_digest: string;
  state: string;
}

interface RepresentativeCallEvidence {
  tool_name?: string;
  result_digest?: string;
  result_size?: number;
  result_type?: string;
  assertions?: {
    result_is_object?: boolean;
    remote_reported_error?: boolean;
  };
}

interface ReviewItem {
  item_id: string;
  server_name: string;
  version: string;
  state: string;
  current_stage: string;
  evidence_digest: string;
  contract_fingerprint: string;
  error_code: string;
  evidence: {
    sop_version?: string;
    snapshot?: { origin?: string };
    effect_proposals?: Record<string, string>;
    tool_schema_digests?: Record<string, string>;
    representative_call?: RepresentativeCallEvidence;
    authorized_scopes?: string[];
    scope_source?: string;
    authorized_scope_digest?: string;
    scope_assessment?: {
      classification?: string;
      dangerous_scopes?: string[];
      unknown_scopes?: string[];
      read_candidate_scopes?: string[];
    };
  };
  proposal: ReviewProposal | null;
  events: ReviewEvent[];
  draft_contract?: { schema_version?: string; contract_id?: string; allowed_tools?: string[] };
}

interface ReviewRun {
  run_id: string;
  trigger?: string;
  status: string;
  cancel_requested: boolean;
  counts: Record<string, number>;
  items: ReviewItem[];
}

interface TrustedMetrics {
  window: string;
  total: number;
  events: Record<string, number>;
  outcomes: Record<string, number>;
}

interface ReviewedContract {
  contract_id: string;
  server_name: string;
  version: string;
  origin: string;
  contract_fingerprint: string;
  allowed_tools: string[];
  revoked: boolean;
  collision: boolean;
}

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
        : detail?.error || "MCP Hub 复核请求失败",
    );
  }
  return payload;
}

const jsonRequest = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export default function McpHubReviewWorkbench({
  status,
  selected,
  onClearSelection,
  onHubChanged,
}: {
  status: HubReviewStatus;
  selected: HubReviewSelection[];
  onClearSelection: () => void;
  onHubChanged: () => void | Promise<void>;
}) {
  const [runs, setRuns] = useState<ReviewRun[]>([]);
  const [contracts, setContracts] = useState<ReviewedContract[]>([]);
  const [metrics, setMetrics] = useState<TrustedMetrics | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [callAcknowledgements, setCallAcknowledgements] = useState<Record<string, boolean>>({});
  const [oauthScopeAcknowledgements, setOauthScopeAcknowledgements] = useState<Record<string, boolean>>({});
  const [allowedToolSelections, setAllowedToolSelections] = useState<Record<string, string[]>>({});
  const [revokeConfirmation, setRevokeConfirmation] = useState("");

  const refresh = useCallback(async () => {
    if (!status.enabled) return;
    try {
      const [runData, contractData, metricData] = await Promise.all([
        requestJson<{ items: ReviewRun[] }>("/api/mcp/hub/review-runs"),
        requestJson<{ items: ReviewedContract[] }>("/api/mcp/hub/contracts"),
        requestJson<TrustedMetrics>("/api/mcp/hub/trusted/metrics?window=30d").catch(() => null),
      ]);
      setRuns(runData.items);
      setContracts(contractData.items);
      setMetrics(metricData);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "复核工作台加载失败");
    }
  }, [status.enabled]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const hasRunning = runs.some((run) => run.status === "queued" || run.status === "running");
    if (!hasRunning) return;
    const timer = window.setInterval(() => void refresh(), 1200);
    return () => window.clearInterval(timer);
  }, [refresh, runs]);

  const runAction = async <T,>(
    key: string,
    operation: () => Promise<T>,
    onSuccess?: (result: T) => void | Promise<void>,
  ) => {
    setBusy(key);
    setError("");
    setNotice("");
    try {
      const result = await operation();
      await onSuccess?.(result);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "复核操作失败");
    } finally {
      setBusy("");
    }
  };

  const latestRun = runs[0];
  const selectedCount = selected.length;
  const publishReady = status.local_publish_enabled && status.signing_key_configured;
  const stageSummary = useMemo(
    () => latestRun?.items.reduce<Record<string, number>>((summary, item) => {
      summary[item.state] = (summary[item.state] || 0) + 1;
      return summary;
    }, {}) || {},
    [latestRun],
  );

  const createRun = () => runAction(
    "create-run",
    () => requestJson<ReviewRun>(
      "/api/mcp/hub/review-runs",
      jsonRequest({
        items: selected.map(({ server_name, version, remote_id }) => ({
          server_name,
          version,
          remote_id,
        })),
      }),
    ),
    () => {
      setNotice("复核批次已创建；自动阶段会在隔离会话内推进，并在代表调用前暂停。 ");
      onClearSelection();
    },
  );

  return (
    <section className="rounded-xl border border-cyan-300/20 bg-cyan-300/[0.045] p-4" data-testid="hub-review-workbench">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-white">
            <ShieldCheck aria-hidden="true" className="text-cyan-200" size={19} />
            <h3 className="font-semibold">复核工作台</h3>
            <span className="rounded border border-amber-300/25 bg-amber-300/10 px-2 py-0.5 text-[11px] font-semibold text-amber-100">本地运维者功能</span>
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            按候选匹配匿名、静态 Token 或 OAuth 的版本化 SOP，生成可复现证据与不可变执行契约。这里不是多租户管理员权限；Registry 与远程 annotations 均不构成信任。
          </p>
        </div>
        <button
          className="flex min-h-10 items-center gap-2 rounded-lg border border-white/10 px-3 text-sm font-semibold text-slate-200 disabled:opacity-40"
          disabled={Boolean(busy)}
          onClick={() => void refresh()}
          type="button"
        >
          <RefreshCw aria-hidden="true" size={15} /> 刷新证据
        </button>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <div className="rounded-lg border border-white/10 bg-ink-950/45 p-3 text-sm text-slate-300">已选择 <strong className="text-white">{selectedCount}</strong> / {status.max_batch_size}</div>
        <div className="rounded-lg border border-white/10 bg-ink-950/45 p-3 text-sm text-slate-300">并发上限 <strong className="text-white">{status.max_concurrency}</strong></div>
        <div className="rounded-lg border border-white/10 bg-ink-950/45 p-3 text-sm text-slate-300">本机发布 <strong className={publishReady ? "text-emerald-200" : "text-amber-200"}>{publishReady ? "可用" : "关闭 / 无签名密钥"}</strong></div>
        <button
          className="min-h-11 rounded-lg bg-cyan-300 px-3 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={selectedCount < 1 || selectedCount > status.max_batch_size || Boolean(status.active_run_id) || Boolean(busy)}
          onClick={() => void createRun()}
          type="button"
        >
          创建受控复核批次
        </button>
      </div>

      {metrics ? (
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 rounded-lg border border-white/10 bg-ink-950/35 px-3 py-2 text-xs text-slate-300" aria-label="最近 30 天可信频道本地漏斗">
          <span>频道查看 <strong className="text-white">{metrics.events.trusted_list_view || 0}</strong></span>
          <span>激活成功 <strong className="text-white">{metrics.events.activation_succeeded || 0}</strong></span>
          <span>审批展示 <strong className="text-white">{metrics.events.runtime_approval_shown || 0}</strong></span>
          <span>调用成功 <strong className="text-white">{metrics.events.runtime_call_succeeded || 0}</strong></span>
          <span>结果未知 <strong className="text-white">{metrics.events.runtime_call_unknown_outcome || 0}</strong></span>
        </div>
      ) : null}

      {selected.length ? (
        <p className="mt-2 break-all text-xs text-slate-500">
          {selected.map((item) => `${item.title} · ${item.origin}`).join(" ｜ ")}
        </p>
      ) : null}
      {error ? <div className="mt-3 flex items-start gap-2 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100" role="alert"><AlertTriangle aria-hidden="true" size={16} />{error}</div> : null}
      {notice ? <div className="mt-3 rounded-lg border border-emerald-300/20 bg-emerald-300/10 px-3 py-2 text-sm text-emerald-100" role="status">{notice}</div> : null}

      {latestRun ? (
        <div className="mt-4 rounded-lg border border-white/10 bg-ink-950/40 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-mono text-xs text-slate-500">{latestRun.run_id}</p>
              <p className="mt-1 text-sm text-slate-200">批次状态：{latestRun.status} · {latestRun.trigger === "automatic" ? "系统自动选批" : latestRun.trigger === "drift" ? "漂移复核" : "人工创建"}</p>
              <p className="mt-1 text-xs text-slate-500">{Object.entries(stageSummary).map(([key, count]) => `${key} ${count}`).join(" · ")}</p>
            </div>
            <div className="flex gap-2">
              {latestRun.status === "interrupted" || latestRun.status === "failed" ? (
                <button className="flex min-h-9 items-center gap-1 rounded-md border border-cyan-300/25 px-3 text-sm text-cyan-100 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void runAction("resume", () => requestJson(`/api/mcp/hub/review-runs/${latestRun.run_id}/resume`, { method: "POST" }))} type="button"><Play aria-hidden="true" size={14} />恢复安全阶段</button>
              ) : null}
              {latestRun.status === "running" || latestRun.status === "queued" || latestRun.status === "awaiting_operator" ? (
                <button className="flex min-h-9 items-center gap-1 rounded-md border border-amber-300/25 px-3 text-sm text-amber-100 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void runAction("cancel", () => requestJson(`/api/mcp/hub/review-runs/${latestRun.run_id}/cancel`, { method: "POST" }))} type="button"><PauseCircle aria-hidden="true" size={14} />取消后续阶段</button>
              ) : null}
            </div>
          </div>

          <div className="mt-3 space-y-3">
            {latestRun.items.map((item) => {
              const readTools = Object.entries(item.evidence.effect_proposals || {})
                .filter(([, effect]) => effect === "read_candidate")
                .map(([name]) => name);
              const defaultAllowedTools = item.proposal && readTools.includes(item.proposal.tool_name)
                ? [item.proposal.tool_name]
                : [];
              const selectedAllowedTools = allowedToolSelections[item.item_id] ?? defaultAllowedTools;
              const representativeCall = item.evidence.representative_call;
              const oauthScopeAssessment = item.evidence.scope_assessment;
              const unknownOAuthScopes = oauthScopeAssessment?.unknown_scopes || [];
              const dangerousOAuthScopes = oauthScopeAssessment?.dangerous_scopes || [];
              return (
                <article className="rounded-lg border border-white/10 bg-white/[0.025] p-3" key={item.item_id}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h4 className="font-semibold text-white">{item.server_name}@{item.version}</h4>
                      <p className="mt-1 text-xs text-slate-500">阶段：{item.current_stage || "queued"} · 状态：{item.state}</p>
                      {item.evidence.snapshot?.origin ? <p className="mt-1 break-all text-xs text-cyan-100">Origin：{item.evidence.snapshot.origin}</p> : null}
                    </div>
                    {item.state === "published" ? <CheckCircle2 aria-label="已发布本机契约" className="text-emerald-200" size={18} /> : null}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {item.events.map((event) => (
                      <span className={`rounded border px-1.5 py-0.5 text-[10px] ${event.status === "failed" || event.status === "blocked" ? "border-rose-300/25 text-rose-100" : "border-white/10 text-slate-400"}`} key={event.event_id} title={`${event.safe_to_retry ? "可安全恢复" : "不可自动重试"} · ${event.status}`}>{event.stage}{event.status === "passed" ? "" : ` · ${event.status}`}</span>
                    ))}
                  </div>
                  {item.error_code ? <p className="mt-2 text-xs text-rose-200">固定错误码：{item.error_code}</p> : null}
                  {item.evidence.sop_version === "oauth_https_tools_v1" ? (
                    <div className="mt-2 rounded border border-violet-300/20 bg-violet-300/5 p-3 text-xs text-slate-300">
                      <p className="font-semibold text-violet-100">OAuth V3 冻结证据</p>
                      <p className="mt-1">Scope 来源：{item.evidence.scope_source} · {item.evidence.authorized_scopes?.join("、") || "未发送 scope 参数"}</p>
                      <p className="mt-1">确定性归类：{oauthScopeAssessment?.classification || "unknown"}</p>
                      {dangerousOAuthScopes.length ? <p className="mt-1 text-rose-100">高危 Scope：{dangerousOAuthScopes.join("、")}；本轮禁止发布。</p> : null}
                      {unknownOAuthScopes.length ? <p className="mt-1 text-amber-100">未知 Scope：{unknownOAuthScopes.join("、")}；批准前必须显式确认。</p> : null}
                    </div>
                  ) : null}
                  {item.proposal ? (
                    <div className="mt-3 rounded-md border border-white/10 bg-ink-950/50 p-3 text-xs text-slate-300">
                      <p>代表调用：<strong className="text-white">{item.proposal.tool_name}</strong></p>
                      <pre className="mt-2 overflow-auto whitespace-pre-wrap break-all text-cyan-100">{JSON.stringify(item.proposal.arguments, null, 2)}</pre>
                      <p className="mt-2 break-all font-mono text-[10px] text-slate-500">Tool Schema：{item.proposal.schema_digest}</p>
                      <p className="mt-2 text-amber-100">
                        确定性风险建议：{item.evidence.effect_proposals?.[item.proposal.tool_name] || "unknown"}。该建议与 Registry 收录都不是安全认证。
                      </p>
                      <p className="mt-2 break-all font-mono text-[10px] text-slate-500">Proposal：{item.proposal.proposal_digest}</p>
                      {item.state === "awaiting_call_approval" ? (
                        <label className="mt-3 flex cursor-pointer items-start gap-2 rounded border border-amber-300/20 bg-amber-300/5 p-2 text-amber-50">
                          <input
                            checked={Boolean(callAcknowledgements[item.item_id])}
                            className="mt-0.5 h-4 w-4 accent-cyan-300"
                            onChange={(event) => setCallAcknowledgements((current) => ({
                              ...current,
                              [item.item_id]: event.target.checked,
                            }))}
                            type="checkbox"
                          />
                          <span>我已核对 Origin、Tool Schema、固定参数与风险提示；确认只调用一次，失败或断链不会自动重试。</span>
                        </label>
                      ) : null}
                    </div>
                  ) : null}
                  {representativeCall?.result_digest ? (
                    <div className="mt-2 rounded border border-emerald-300/20 bg-emerald-300/5 p-3 text-xs text-emerald-100">
                      <p className="font-semibold">代表调用已完成：单次执行，临时会话已清理</p>
                      <p className="mt-1">{representativeCall.result_type || "result"} · {representativeCall.result_size || 0} bytes · 远程错误：{representativeCall.assertions?.remote_reported_error ? "是" : "否"}</p>
                      <p className="mt-1 break-all font-mono text-[10px] text-emerald-200/70">Result：{representativeCall.result_digest}</p>
                    </div>
                  ) : null}
                  {previews[item.item_id] ? (
                    <details className="mt-2 rounded border border-white/10 bg-ink-950/45 p-2 text-xs text-slate-300">
                      <summary className="cursor-pointer font-semibold text-cyan-100">查看脱敏临时预览（最多 4 KiB）</summary>
                      <pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap break-all text-slate-300">{previews[item.item_id]}</pre>
                    </details>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {item.state === "awaiting_call_approval" && item.proposal ? (
                      <button className="min-h-9 rounded-md bg-cyan-300 px-3 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={!callAcknowledgements[item.item_id] || Boolean(busy)} onClick={() => void runAction(`call:${item.item_id}`, () => requestJson<{ preview: string }>(`/api/mcp/hub/review-runs/${latestRun.run_id}/items/${item.item_id}/call-proposals/${item.proposal!.proposal_id}/approve`, jsonRequest({ expected_proposal_digest: item.proposal!.proposal_digest })), (result) => {
                        setPreviews((current) => ({ ...current, [item.item_id]: result.preview }));
                        setCallAcknowledgements((current) => ({ ...current, [item.item_id]: false }));
                      })} type="button">逐次批准代表调用</button>
                    ) : null}
                    {item.state === "awaiting_decision" ? (
                      <>
                        <div className="w-full rounded-md border border-white/10 bg-ink-950/35 p-3 text-xs text-slate-300">
                          <p className="font-semibold text-white">选择发布工具子集</p>
                          <p className="mt-1 text-slate-500">默认仅选择已完成代表调用的工具；其他工具必须由运维者逐项决定，非 read_candidate 不可发布。</p>
                          <div className="mt-2 grid gap-2 sm:grid-cols-2">
                            {Object.entries(item.evidence.effect_proposals || {}).sort(([left], [right]) => left.localeCompare(right)).map(([name, effect]) => {
                              const eligible = effect === "read_candidate";
                              return (
                                <label className={`flex items-start gap-2 rounded border p-2 ${eligible ? "border-cyan-300/20 text-cyan-50" : "border-white/10 text-slate-500"}`} key={name}>
                                  <input
                                    aria-label={`允许工具 ${name}`}
                                    checked={selectedAllowedTools.includes(name)}
                                    className="mt-0.5 h-4 w-4 accent-cyan-300"
                                    disabled={!eligible}
                                    onChange={(event) => setAllowedToolSelections((current) => {
                                      const base = current[item.item_id] ?? defaultAllowedTools;
                                      const next = event.target.checked
                                        ? [...new Set([...base, name])]
                                        : base.filter((toolName) => toolName !== name);
                                      return { ...current, [item.item_id]: next };
                                    })}
                                    type="checkbox"
                                  />
                                  <span><strong className="font-semibold">{name}</strong><br /><span className="text-[10px]">{effect}</span></span>
                                </label>
                              );
                            })}
                          </div>
                          {unknownOAuthScopes.length ? (
                            <label className="mt-3 flex items-start gap-2 rounded border border-amber-300/20 bg-amber-300/5 p-2 text-amber-100">
                              <input
                                checked={Boolean(oauthScopeAcknowledgements[item.item_id])}
                                onChange={(event) => setOauthScopeAcknowledgements((current) => ({ ...current, [item.item_id]: event.target.checked }))}
                                type="checkbox"
                              />
                              <span>我已核对未知 Scope 的供应商含义；契约仍只包含人工确认的 read 工具。</span>
                            </label>
                          ) : null}
                        </div>
                        <button className="min-h-9 rounded-md bg-emerald-300 px-3 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={!selectedAllowedTools.length || dangerousOAuthScopes.length > 0 || (unknownOAuthScopes.length > 0 && !oauthScopeAcknowledgements[item.item_id]) || Boolean(busy)} onClick={() => void runAction(`decision:${item.item_id}`, () => requestJson(`/api/mcp/hub/review-runs/${latestRun.run_id}/items/${item.item_id}/decision`, jsonRequest({ decision: "approve", expected_evidence_digest: item.evidence_digest, allowed_tools: selectedAllowedTools, tool_effects: Object.fromEntries(selectedAllowedTools.map((name) => [name, "read"])), acknowledge_unknown_oauth_scopes: Boolean(oauthScopeAcknowledgements[item.item_id]) })))} type="button">批准所选只读工具</button>
                        <button className="min-h-9 rounded-md border border-rose-300/25 px-3 text-sm text-rose-100 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void runAction(`block:${item.item_id}`, () => requestJson(`/api/mcp/hub/review-runs/${latestRun.run_id}/items/${item.item_id}/decision`, jsonRequest({ decision: "block", expected_evidence_digest: item.evidence_digest, allowed_tools: [], tool_effects: {} })))} type="button">阻断</button>
                      </>
                    ) : null}
                    {item.state === "approved" ? (
                      <button className="min-h-9 rounded-md bg-emerald-300 px-3 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={!publishReady || Boolean(busy)} onClick={() => void runAction(`publish:${item.item_id}`, () => requestJson(`/api/mcp/hub/review-runs/${latestRun.run_id}/items/${item.item_id}/publish`, jsonRequest({ expected_contract_fingerprint: item.contract_fingerprint })), async () => {
                        await onHubChanged();
                        setNotice(item.evidence.sop_version === "oauth_https_tools_v1" ? "OAuth V3 契约已发布；R3A 仍保持 Runtime 关闭。" : "本机不可变契约已发布；对应候选现已可进入激活门禁。");
                      })} type="button">发布本机不可变契约</button>
                    ) : null}
                    {item.state === "approved" || item.state === "published" ? (
                      <a className="flex min-h-9 items-center gap-1 rounded-md border border-white/10 px-3 text-sm text-slate-200" download href={`/api/mcp/hub/review-runs/${latestRun.run_id}/items/${item.item_id}/contract-export`}><Download aria-hidden="true" size={14} />导出仓库契约</a>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      ) : <p className="mt-4 text-sm text-slate-500">尚无复核批次。请在 Registry 列表勾选 1–20 个可试连版本。</p>}

      {contracts.length ? (
        <div className="mt-4 border-t border-white/10 pt-4">
          <h4 className="text-sm font-semibold text-white">已加载执行契约</h4>
          <div className="mt-2 grid gap-2 lg:grid-cols-2">
            {contracts.map((contract) => (
              <div className="rounded-md border border-white/10 bg-ink-950/40 p-3 text-xs text-slate-400" key={contract.contract_id}>
                <p className="font-semibold text-white">{contract.server_name}@{contract.version}</p>
                <p className="mt-1 break-all">{contract.origin}</p>
                <p className="mt-1">工具：{contract.allowed_tools.join("、")}</p>
                <div className="mt-2 flex items-center justify-between gap-2">
                  <span className={contract.collision || contract.revoked ? "text-rose-200" : "text-emerald-200"}>{contract.collision ? "契约碰撞：已关闭" : contract.revoked ? "已撤销" : "可用于激活门禁"}</span>
                  {!contract.revoked && revokeConfirmation !== contract.contract_id ? (
                    <button className="flex items-center gap-1 rounded border border-rose-300/20 px-2 py-1 text-rose-100 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => setRevokeConfirmation(contract.contract_id)} type="button"><Ban aria-hidden="true" size={12} />撤销并断开</button>
                  ) : null}
                  {!contract.revoked && revokeConfirmation === contract.contract_id ? (
                    <div className="basis-full rounded border border-rose-300/20 bg-rose-300/5 p-2" role="alert">
                      <p className="text-rose-100">撤销后会立即断开对应 Hub 会话。</p>
                      <div className="mt-2 flex flex-wrap justify-end gap-2">
                        <button className="rounded bg-rose-300 px-2 py-1 font-semibold text-ink-950 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void runAction(`revoke:${contract.contract_id}`, () => requestJson(`/api/mcp/hub/contracts/${contract.contract_id}/revoke`, jsonRequest({ reason: "local operator revocation" })), async () => {
                          setRevokeConfirmation("");
                          await onHubChanged();
                          setNotice("执行契约已撤销，对应 Hub 会话已断开。");
                        })} type="button">确认撤销</button>
                        <button className="rounded border border-white/10 px-2 py-1 text-slate-200 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => setRevokeConfirmation("")} type="button">保留契约</button>
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
