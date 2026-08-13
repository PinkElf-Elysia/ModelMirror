import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  McpAvailability,
  McpBrowserPolicy,
  McpDatabasePreflightStatus,
} from "../data/mcpAdaptationPlan";

interface McpBrowserSessionStatus {
  status: "active" | "tainted" | "disconnected";
  generation?: string;
  page_revision?: number;
  page_digest?: string;
  current_origin?: string | null;
  action_count?: number;
  max_actions?: number;
  expires_at?: number | string | null;
  approved_hosts?: string[];
}

interface McpBrowserArtifact {
  artifact_id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  created_at: number | string;
  expires_at: number | string;
  download_url?: string;
}

interface McpBrowserPanelProps {
  availability: McpAvailability;
  compact?: boolean;
  connected: boolean;
  policy: McpBrowserPolicy | null;
  preflightStatus: McpDatabasePreflightStatus;
  projectId: string;
  refreshKey?: number | string;
}

const preflightCopy: Record<
  McpDatabasePreflightStatus,
  { label: string; className: string }
> = {
  "not-applicable": { label: "尚未开始", className: "text-slate-300" },
  blocked: { label: "浏览器预检已阻断", className: "text-rose-100" },
  "awaiting-workspace": { label: "等待浏览器策略", className: "text-amber-100" },
  "awaiting-configuration": { label: "等待受控配置", className: "text-amber-100" },
  unverified: { label: "等待连接预检", className: "text-amber-100" },
  verifying: { label: "正在校验浏览器与工具契约", className: "text-cyan-100" },
  verified: { label: "隔离、网络与工具契约通过", className: "text-emerald-100" },
  failed: { label: "浏览器预检失败", className: "text-rose-100" },
};

const sessionCopy: Record<
  McpBrowserSessionStatus["status"],
  { label: string; className: string }
> = {
  active: { label: "临时会话运行中", className: "text-emerald-100" },
  tainted: { label: "结果未知，会话已隔离", className: "text-rose-100" },
  disconnected: { label: "尚未连接", className: "text-slate-300" },
};

function asMilliseconds(value: number | string | null | undefined) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return value > 10_000_000_000 ? value : value * 1000;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return numeric > 10_000_000_000 ? numeric : numeric * 1000;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatDate(value: number | string | null | undefined) {
  const milliseconds = asMilliseconds(value);
  if (milliseconds === null) return "未提供";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(milliseconds);
}

function formatDuration(seconds: number) {
  if (seconds % 86_400 === 0) return `${seconds / 86_400} 天`;
  if (seconds % 3_600 === 0) return `${seconds / 3_600} 小时`;
  if (seconds % 60 === 0) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

function safeDownloadUrl(value: string | undefined, projectId: string) {
  if (!value) return null;
  try {
    const parsed = new URL(value, window.location.origin);
    const prefix = `/api/mcp/catalog/${encodeURIComponent(projectId)}/browser-artifacts/`;
    if (
      parsed.origin !== window.location.origin ||
      !parsed.pathname.startsWith(prefix) ||
      !parsed.pathname.endsWith("/download")
    ) return null;
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}

async function readError(response: Response) {
  try {
    const data = (await response.json()) as { detail?: string | { message?: string } };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail?.message) return data.detail.message;
  } catch {
    // Fall back to the HTTP status below.
  }
  return response.statusText || `HTTP ${response.status}`;
}

export default function McpBrowserPanel({
  availability,
  compact = false,
  connected,
  policy,
  preflightStatus,
  projectId,
  refreshKey = 0,
}: McpBrowserPanelProps) {
  const [session, setSession] = useState<McpBrowserSessionStatus | null>(null);
  const [artifacts, setArtifacts] = useState<McpBrowserArtifact[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [deleteCandidate, setDeleteCandidate] = useState<string | null>(null);
  const [deletingArtifact, setDeletingArtifact] = useState<string | null>(null);

  const loadState = useCallback(async (signal?: AbortSignal) => {
    if (availability !== "ready" || !policy) return;
    setLoading(true);
    try {
      const [sessionResponse, artifactsResponse] = await Promise.all([
        fetch(`/api/mcp/catalog/${projectId}/browser-session`, { signal }),
        fetch(`/api/mcp/catalog/${projectId}/browser-artifacts`, { signal }),
      ]);
      if (sessionResponse.ok) {
        setSession((await sessionResponse.json()) as McpBrowserSessionStatus);
      } else if (sessionResponse.status === 404 || sessionResponse.status === 409) {
        setSession({ status: "disconnected" });
      } else {
        throw new Error(await readError(sessionResponse));
      }

      if (artifactsResponse.ok) {
        const data = (await artifactsResponse.json()) as {
          items?: McpBrowserArtifact[];
        };
        setArtifacts(Array.isArray(data.items) ? data.items : []);
      } else if (artifactsResponse.status === 404) {
        setArtifacts([]);
      } else {
        throw new Error(await readError(artifactsResponse));
      }
      setError("");
    } catch (exc) {
      if (exc instanceof DOMException && exc.name === "AbortError") return;
      setError(exc instanceof Error ? exc.message : "无法读取浏览器会话状态");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [availability, policy, projectId]);

  useEffect(() => {
    const controller = new AbortController();
    void loadState(controller.signal);
    return () => controller.abort();
  }, [loadState, refreshKey]);

  useEffect(() => {
    if (!connected || availability !== "ready" || !policy) return;
    const timer = window.setInterval(() => void loadState(), 10_000);
    return () => window.clearInterval(timer);
  }, [availability, connected, loadState, policy]);

  const status = session?.status ?? (connected ? "active" : "disconnected");
  const actionCount = session?.action_count ?? 0;
  const maxActions = session?.max_actions ?? policy?.max_actions ?? 50;
  const approvedHosts = session?.approved_hosts ?? [];
  const policyDisabledCapabilities = useMemo(
    () => [
      ["账号凭据采集、登录流程与登录态持久化", policy?.login_state],
      ["上传", policy?.uploads],
      ["下载", policy?.downloads],
      ["剪贴板", policy?.clipboard],
      ["本机文件", policy?.local_files],
      ["Cookie 导入、导出与持久化", policy?.cookies],
      ["Storage 导入、导出与持久化", policy?.storage],
      ["任意脚本求值工具", policy?.evaluate],
      ["外部 CDP", policy?.cdp],
    ].filter((entry) => entry[1] === false).map((entry) => entry[0] as string),
    [policy],
  );

  async function deleteArtifact(artifactId: string) {
    setDeletingArtifact(artifactId);
    setError("");
    try {
      const response = await fetch(
        `/api/mcp/catalog/${projectId}/browser-artifacts/${encodeURIComponent(artifactId)}`,
        { method: "DELETE" },
      );
      if (!response.ok && response.status !== 404) throw new Error(await readError(response));
      setArtifacts((current) => current.filter((item) => item.artifact_id !== artifactId));
      setDeleteCandidate(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "截图清理失败");
    } finally {
      setDeletingArtifact(null);
    }
  }

  if (availability === "blocked") {
    return (
      <section
        aria-label="浏览器适配状态"
        className="relative mt-3 rounded-lg border border-rose-300/20 bg-rose-300/[0.055] p-3 text-xs"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold text-rose-100">浏览器运行时未启动</h3>
          <span className="rounded-full border border-rose-300/25 bg-rose-300/[0.08] px-2.5 py-1 font-semibold text-rose-100">
            连接与安装关闭
          </span>
        </div>
        <p className="mt-2 leading-5 text-slate-300">
          该条目尚未形成可维护、可审计的固定浏览器契约。页面不会启动浏览器进程，也不会回退到外部 CDP、WebDriver 或归档运行时。
        </p>
      </section>
    );
  }

  if (!policy) {
    return (
      <section
        aria-label="浏览器适配状态"
        className="relative mt-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.055] p-3 text-xs text-amber-50"
      >
        正在同步服务端浏览器安全策略；策略可用前连接入口保持关闭。
      </section>
    );
  }

  return (
    <section
      aria-label={compact ? "Playwright 会话与截图" : "临时浏览器安全与会话状态"}
      className="relative mt-3 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.05] p-3 text-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-cyan-100">{compact ? "会话与截图" : "临时浏览器安全状态"}</h3>
        {!compact ? <div className="flex flex-wrap gap-2">
          <span className="rounded-full border border-cyan-300/25 bg-cyan-300/[0.08] px-2.5 py-1 font-semibold text-cyan-100">
            匿名临时会话
          </span>
          <span className="rounded-full border border-emerald-300/25 bg-emerald-300/[0.08] px-2.5 py-1 font-semibold text-emerald-100">
            仅截图产物
          </span>
        </div> : null}
      </div>

      <dl className="mt-3 grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg bg-black/15 p-2.5">
          <dt className="text-slate-400">会话</dt>
          <dd aria-live="polite" className={`mt-1 font-semibold ${sessionCopy[status].className}`}>
            {loading && !session ? "正在读取状态" : sessionCopy[status].label}
          </dd>
        </div>
        {!compact ? <div className="rounded-lg bg-black/15 p-2.5">
          <dt className="text-slate-400">浏览器预检</dt>
          <dd aria-live="polite" className={`mt-1 font-semibold ${preflightCopy[preflightStatus].className}`}>
            {preflightCopy[preflightStatus].label}
          </dd>
        </div> : null}
        <div className="rounded-lg bg-black/15 p-2.5">
          <dt className="text-slate-400">操作额度</dt>
          <dd className="mt-1 font-semibold text-white">{actionCount} / {maxActions}</dd>
          <progress
            aria-label={`已执行 ${actionCount} 次，最多 ${maxActions} 次`}
            className="mt-2 h-1.5 w-full accent-cyan-300"
            max={Math.max(1, maxActions)}
            value={Math.min(actionCount, maxActions)}
          />
        </div>
      </dl>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg bg-black/15 p-2.5">
          <p className="text-slate-400">当前页面来源</p>
          <p className="mt-1 break-all font-medium text-slate-100">
            {session?.current_origin || "尚未导航"}
          </p>
          {!compact ? <p className="mt-1 text-slate-400">
            页面版本 {session?.page_revision ?? 0}；跨域后旧审批自动失效。
          </p> : null}
        </div>
        <div className="rounded-lg bg-black/15 p-2.5">
          <p className="text-slate-400">会话有效期</p>
          <p className="mt-1 font-medium text-slate-100">
            {status === "active" ? `最晚 ${formatDate(session?.expires_at)} 结束` : "连接后开始计时"}
          </p>
          {!compact ? <p className="mt-1 text-slate-400">
            最长 {formatDuration(policy.session_ttl_seconds)}；闲置 {formatDuration(policy.idle_ttl_seconds)} 自动清理。
          </p> : null}
        </div>
      </div>

      {approvedHosts.length > 0 ? (
        <div className="mt-3">
          <p className="text-slate-400">本次会话已批准目标域</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {approvedHosts.map((host) => (
              <span className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-slate-200" key={host}>
                {host}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {!compact ? <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.035] p-3 text-slate-300">
        <p className="font-semibold text-white">固定边界</p>
        <p className="mt-1 leading-5">
          仅允许公网 {policy.allowed_schemes.map((item) => item.toUpperCase()).join("/")} 与端口 {policy.allowed_ports.join("、")}；DNS 固定后连接，跨 origin 请求与重定向直接拒绝。最多 {policy.max_pages} 个页面，全局最多 {policy.max_concurrent_sessions} 个并发会话。
        </p>
        <p className="mt-1 leading-5 text-slate-400">
          导航超时 {policy.navigation_timeout_seconds} 秒，工具调用超时 {policy.call_timeout_seconds} 秒；出口最多 {policy.max_tunnels_per_session} 个并发隧道、累计 {formatBytes(policy.max_egress_bytes_per_session)}，隧道闲置 {policy.egress_tunnel_idle_seconds} 秒或持续 {policy.egress_tunnel_ttl_seconds} 秒即关闭。
        </p>
        <p className="mt-1 leading-5 text-slate-400">
          单次工具结果最多 {formatBytes(policy.max_output_bytes)}。网页自身产生的 Cookie、缓存和站点存储只存在于临时 profile，结束会话时删除。
        </p>
        <p className="mt-2 leading-5 text-slate-400">
          已关闭：{policyDisabledCapabilities.join("、")}。
        </p>
        <p className="mt-2 leading-5 text-amber-100">
          页面仍可能自行呈现登录界面；本批不会把它作为可用能力，也不会采集、继承或保存登录态。请勿输入账号、密码、OTP 或其他认证信息。
        </p>
      </div> : null}

      <div className="mt-3 border-t border-white/10 pt-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="font-semibold text-white">截图产物</p>
            {!compact ? <p className="mt-1 text-slate-400">
              单张最多 {formatBytes(policy.max_artifact_bytes)}；每项目最多 {policy.max_artifacts_per_project} 张 / {formatBytes(policy.max_artifact_storage_bytes)}，默认保留 {formatDuration(policy.artifact_ttl_seconds)}。
            </p> : null}
          </div>
          <button
            className="min-h-11 rounded-full border border-cyan-300/20 px-3 py-2 font-semibold text-cyan-100 transition hover:border-cyan-300/35 hover:bg-cyan-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={loading}
            onClick={() => void loadState()}
            type="button"
          >
            {loading ? "正在刷新" : "刷新状态"}
          </button>
        </div>

        {artifacts.length === 0 ? (
          <p className="mt-3 rounded-lg bg-black/15 p-3 leading-5 text-slate-400">
            暂无截图。执行截图工具后，下载和清理入口会显示在这里。
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {artifacts.map((artifact) => (
              <li className="rounded-lg bg-black/15 p-3" key={artifact.artifact_id}>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-white">{artifact.name}</p>
                    <p className="mt-1 text-slate-400">
                      {formatBytes(artifact.size_bytes)} · {artifact.mime_type} · 到期 {formatDate(artifact.expires_at)}
                    </p>
                    {!compact ? <p className="mt-1 truncate font-mono text-[11px] text-slate-400">
                      SHA-256 {artifact.sha256.slice(0, 16)}…
                    </p> : null}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {safeDownloadUrl(artifact.download_url, projectId) ? (
                      <a
                        className="inline-flex min-h-11 items-center rounded-full bg-cyan-300 px-3 py-2 font-semibold text-ink-950 transition hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100"
                        download
                        href={safeDownloadUrl(artifact.download_url, projectId) ?? undefined}
                      >
                        下载截图
                      </a>
                    ) : null}
                    {deleteCandidate === artifact.artifact_id ? (
                      <>
                        <button
                          className="min-h-11 rounded-full border border-white/10 px-3 py-2 font-semibold text-slate-200 transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
                          onClick={() => setDeleteCandidate(null)}
                          type="button"
                        >
                          取消
                        </button>
                        <button
                          className="min-h-11 rounded-full border border-rose-300/30 bg-rose-300/10 px-3 py-2 font-semibold text-rose-100 transition hover:bg-rose-300/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-200 disabled:opacity-50"
                          disabled={deletingArtifact === artifact.artifact_id}
                          onClick={() => void deleteArtifact(artifact.artifact_id)}
                          type="button"
                        >
                          {deletingArtifact === artifact.artifact_id ? "正在清理" : "确认清理"}
                        </button>
                      </>
                    ) : (
                      <button
                        className="min-h-11 rounded-full border border-rose-300/25 px-3 py-2 font-semibold text-rose-100 transition hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-200"
                        onClick={() => setDeleteCandidate(artifact.artifact_id)}
                        type="button"
                      >
                        清理截图
                      </button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {error ? (
        <p aria-live="polite" className="mt-3 rounded-lg border border-rose-300/25 bg-rose-300/[0.08] p-3 leading-5 text-rose-100" role="status">
          状态读取失败：{error}
        </p>
      ) : null}
    </section>
  );
}
