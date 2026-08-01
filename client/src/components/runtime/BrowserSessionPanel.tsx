import { useCallback, useEffect, useState } from "react";

interface BrowserGrant {
  domain: string;
  created_at: number;
}

interface BrowserOperation {
  operation_id: string;
  tool_name: string;
  status: string;
  page_title?: string;
  error?: string | null;
}

interface BrowserArtifact {
  artifact_id: string;
  filename: string;
  size_bytes: number;
  kind: string;
}

interface BrowserSession {
  session_id: string;
  scope_id: string;
  status: string;
  current_url: string;
  current_domain: string;
  page_title: string;
  action_count: number;
  max_actions: number;
  grants: BrowserGrant[];
  operations?: BrowserOperation[];
  artifacts?: BrowserArtifact[];
}

interface BrowserSessionPanelProps {
  scopeType: "workflow" | "conversation" | "goal" | "handoff";
  scopeId?: string;
  scopeIdPrefix?: string;
  compact?: boolean;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function operationTone(status: string) {
  if (status === "completed") return "text-emerald-200";
  if (status === "failed") return "text-rose-200";
  return "text-amber-200";
}

function statusLabel(status: string) {
  if (status === "active") return "运行中";
  if (status === "closed") return "已关闭";
  if (status === "expired") return "已过期";
  return status;
}

export default function BrowserSessionPanel({
  scopeType,
  scopeId,
  scopeIdPrefix,
  compact = false,
}: BrowserSessionPanelProps) {
  const [sessions, setSessions] = useState<BrowserSession[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const refresh = useCallback(async () => {
    if (!scopeId && !scopeIdPrefix) return;
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ scope_type: scopeType, limit: "50" });
      if (scopeId) query.set("scope_id", scopeId);
      const response = await fetch(`/api/runtime/browser-sessions?${query}`);
      if (!response.ok) throw new Error("浏览器会话暂不可用");
      const payload = (await response.json()) as { items?: BrowserSession[] };
      const matched = (payload.items ?? []).filter(
        (item) => !scopeIdPrefix || item.scope_id.startsWith(scopeIdPrefix),
      );
      const details = await Promise.all(
        matched.map(async (item) => {
          const detail = await fetch(
            `/api/runtime/browser-sessions/${encodeURIComponent(item.session_id)}`,
          );
          return detail.ok ? ((await detail.json()) as BrowserSession) : item;
        }),
      );
      setSessions(details);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "浏览器会话暂不可用");
    } finally {
      setLoading(false);
    }
  }, [scopeId, scopeIdPrefix, scopeType]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function revokeGrant(sessionId: string, domain: string) {
    setBusyId(`${sessionId}:${domain}`);
    setError("");
    try {
      const response = await fetch(
        `/api/runtime/browser-sessions/${encodeURIComponent(sessionId)}/grants/${encodeURIComponent(domain)}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error("撤销域名授权失败");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "撤销域名授权失败");
    } finally {
      setBusyId("");
    }
  }

  async function closeSession(sessionId: string) {
    setBusyId(sessionId);
    setError("");
    try {
      const response = await fetch(
        `/api/runtime/browser-sessions/${encodeURIComponent(sessionId)}/close`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error("关闭浏览器会话失败");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "关闭浏览器会话失败");
    } finally {
      setBusyId("");
    }
  }

  if (!scopeId && !scopeIdPrefix) return null;

  return (
    <section className="border-t border-white/10 bg-slate-950/20">
      <button
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-white/[0.04]"
        onClick={() => setExpanded((value) => !value)}
        type="button"
      >
        <span>
          <span className="block text-xs font-semibold text-slate-200">隔离浏览器</span>
          <span className="mt-0.5 block text-[11px] text-slate-500">
            {loading ? "正在同步" : `${sessions.length} 个会话`}
          </span>
        </span>
        <span className="text-[11px] text-slate-500">{expanded ? "收起" : "展开"}</span>
      </button>

      {expanded ? (
        <div className={`space-y-2 px-4 pb-4 ${compact ? "max-h-96 overflow-y-auto" : ""}`}>
          {error ? (
            <p className="rounded-md bg-rose-300/10 px-3 py-2 text-xs text-rose-100">{error}</p>
          ) : sessions.length === 0 ? (
            <p className="rounded-md border border-dashed border-white/15 px-3 py-4 text-center text-xs text-slate-500">
              Agent 使用 Browser 工具后，页面状态与下载产物会显示在这里。
            </p>
          ) : (
            sessions.map((session) => (
              <article className="rounded-md bg-white/[0.045] p-3" key={session.session_id}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-semibold text-slate-200">
                      {session.page_title || session.current_domain || "尚未导航"}
                    </p>
                    <p className="mt-1 truncate font-mono text-[10px] text-slate-500">
                      {session.current_url || session.scope_id}
                    </p>
                  </div>
                  <span className="shrink-0 rounded-full bg-cyan-300/10 px-2 py-0.5 text-[10px] text-cyan-100">
                    {statusLabel(session.status)}
                  </span>
                </div>

                <div className="mt-2 flex items-center justify-between text-[10px] text-slate-500">
                  <span>{session.action_count}/{session.max_actions} 次操作</span>
                  {session.status === "active" ? (
                    <button
                      className="text-slate-400 transition hover:text-white disabled:opacity-50"
                      disabled={busyId === session.session_id}
                      onClick={() => void closeSession(session.session_id)}
                      type="button"
                    >
                      关闭会话
                    </button>
                  ) : null}
                </div>

                {session.grants.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-1.5 border-t border-white/10 pt-2">
                    {session.grants.map((grant) => (
                      <button
                        className="rounded-md border border-white/10 bg-black/15 px-2 py-1 text-[10px] text-slate-300 transition hover:border-rose-300/30 hover:text-rose-100 disabled:opacity-50"
                        disabled={busyId === `${session.session_id}:${grant.domain}`}
                        key={grant.domain}
                        onClick={() => void revokeGrant(session.session_id, grant.domain)}
                        title="撤销后再次访问需要重新审批"
                        type="button"
                      >
                        {grant.domain} 撤销
                      </button>
                    ))}
                  </div>
                ) : null}

                {(session.operations ?? []).length > 0 ? (
                  <div className="mt-2 space-y-1 border-t border-white/10 pt-2">
                    {(session.operations ?? []).slice(0, 6).map((operation) => (
                      <p
                        className={`truncate text-[11px] ${operationTone(operation.status)}`}
                        key={operation.operation_id}
                        title={operation.error ?? undefined}
                      >
                        {operation.tool_name} · {operation.status}
                        {operation.page_title ? ` · ${operation.page_title}` : ""}
                      </p>
                    ))}
                  </div>
                ) : null}

                {(session.artifacts ?? []).length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-2 border-t border-white/10 pt-2">
                    {(session.artifacts ?? []).map((artifact) =>
                      artifact.kind === "screenshot" ? (
                        <a
                          className="rounded-md bg-violet-300/10 px-2.5 py-1 text-[11px] text-violet-100 transition hover:bg-violet-300/20"
                          href={`/api/runtime/browser-sessions/${encodeURIComponent(session.session_id)}/screenshot`}
                          key={artifact.artifact_id}
                          rel="noreferrer"
                          target="_blank"
                        >
                          查看截图
                        </a>
                      ) : (
                        <a
                          className="rounded-md bg-cyan-300/10 px-2.5 py-1 text-[11px] text-cyan-100 transition hover:bg-cyan-300/20"
                          href={`/api/runtime/browser-artifacts/${encodeURIComponent(artifact.artifact_id)}/download`}
                          key={artifact.artifact_id}
                        >
                          下载 {artifact.filename} ({formatBytes(artifact.size_bytes)})
                        </a>
                      ),
                    )}
                  </div>
                ) : null}
              </article>
            ))
          )}

          <button
            className="text-[11px] text-cyan-200 hover:text-cyan-100"
            onClick={() => void refresh()}
            type="button"
          >
            刷新浏览器会话
          </button>
        </div>
      ) : null}
    </section>
  );
}
