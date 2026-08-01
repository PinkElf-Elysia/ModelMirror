import { useCallback, useEffect, useRef, useState } from "react";

interface ClientToolRequest {
  request_id: string;
  host_id: string;
  task_id: string;
  scope_type: string;
  scope_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  mutating: boolean;
  status: string;
  error?: string | null;
  result_length: number;
  result_metadata?: { artifact_id?: string };
  host_type?: "chrome" | "office";
  office_app?: "word" | "excel" | "powerpoint" | null;
  document_binding?: {
    bound: boolean;
    title?: string;
    binding_id?: string;
  } | null;
  updated_at: number;
}

interface ClientToolPanelProps {
  taskId?: string;
  scopeType?: "workflow" | "conversation" | "goal" | "handoff";
  scopeId?: string;
  scopeIdPrefix?: string;
  compact?: boolean;
  onResolved?: () => void | Promise<void>;
}

const waitingStatuses = new Set(["pending", "dispatched", "running"]);
const retryStatuses = new Set(["failed", "cancelled", "expired", "uncertain"]);

function statusTone(status: string) {
  if (status === "completed") return "border-emerald-300/20 bg-emerald-300/10 text-emerald-100";
  if (retryStatuses.has(status)) return "border-rose-300/20 bg-rose-300/10 text-rose-100";
  return "border-amber-300/20 bg-amber-300/10 text-amber-100";
}

function statusLabel(status: string) {
  return ({
    pending: "等待宿主",
    dispatched: "已派发",
    running: "客户端执行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    expired: "已超时",
    uncertain: "结果不确定",
  } as Record<string, string>)[status] ?? status;
}

export default function ClientToolPanel({
  taskId,
  scopeType,
  scopeId,
  scopeIdPrefix,
  compact = false,
  onResolved,
}: ClientToolPanelProps) {
  const [requests, setRequests] = useState<ClientToolRequest[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const seenTerminal = useRef(new Set<string>());
  const initialized = useRef(false);

  const refresh = useCallback(async () => {
    if (!taskId && (!scopeType || (!scopeId && !scopeIdPrefix))) return;
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (taskId) query.set("task_id", taskId);
      if (scopeType) query.set("scope_type", scopeType);
      if (scopeId) query.set("scope_id", scopeId);
      const response = await fetch(`/api/runtime/client-tool-requests?${query}`);
      if (!response.ok) throw new Error("客户端工具请求暂不可用");
      const payload = (await response.json()) as { requests?: ClientToolRequest[] };
      const next = (payload.requests ?? []).filter(
        (item) => !scopeIdPrefix || item.scope_id.startsWith(scopeIdPrefix),
      );
      const newlyResolved = next.filter(
        (item) => !waitingStatuses.has(item.status) && !seenTerminal.current.has(item.request_id),
      );
      newlyResolved.forEach((item) => seenTerminal.current.add(item.request_id));
      setRequests(next);
      if (initialized.current && newlyResolved.length > 0) await onResolved?.();
      initialized.current = true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "客户端工具请求暂不可用");
    } finally {
      setLoading(false);
    }
  }, [onResolved, scopeId, scopeIdPrefix, scopeType, taskId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!requests.some((item) => waitingStatuses.has(item.status))) return;
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [refresh, requests]);

  async function mutate(requestId: string, action: "retry" | "cancel") {
    setBusyId(requestId);
    setError("");
    try {
      const response = await fetch(
        `/api/runtime/client-tool-requests/${encodeURIComponent(requestId)}/${action}`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error(action === "retry" ? "重新派发失败" : "取消失败");
      seenTerminal.current.delete(requestId);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "客户端工具操作失败");
    } finally {
      setBusyId("");
    }
  }

  if (!taskId && (!scopeType || (!scopeId && !scopeIdPrefix))) return null;

  return (
    <section className="border-t border-white/10 bg-slate-950/20">
      <button
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-white/[0.04]"
        onClick={() => setExpanded((value) => !value)}
        type="button"
      >
        <span>
          <span className="block text-xs font-semibold text-slate-200">客户端工具</span>
          <span className="mt-0.5 block text-[11px] text-slate-500">
            {loading ? "正在同步" : `${requests.length} 个请求`}
          </span>
        </span>
        <span className="text-[11px] text-slate-500">{expanded ? "收起" : "展开"}</span>
      </button>

      {expanded ? (
        <div className={`space-y-2 px-4 pb-4 ${compact ? "max-h-96 overflow-y-auto" : ""}`}>
          {error ? <p className="rounded-md bg-rose-300/10 px-3 py-2 text-xs text-rose-100">{error}</p> : null}
          {requests.length === 0 ? (
            <p className="rounded-md border border-dashed border-white/15 px-3 py-4 text-center text-xs text-slate-500">
              Agent 请求已配对 Chrome 宿主后，等待与执行状态会显示在这里。
            </p>
          ) : requests.map((request) => (
            <article className="rounded-md bg-white/[0.045] p-3" key={request.request_id}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold text-slate-200">{request.tool_name}</p>
                  <p className="mt-1 truncate font-mono text-[10px] text-slate-500">{request.host_id}</p>
                  {request.host_type === "office" ? (
                    <p className="mt-1 truncate text-[10px] text-emerald-200/80">
                      Office {request.office_app ?? "host"} · {request.document_binding?.title || "当前文档未绑定"}
                    </p>
                  ) : null}
                </div>
                <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${statusTone(request.status)}`}>
                  {statusLabel(request.status)}
                </span>
              </div>
              {Object.keys(request.arguments ?? {}).length > 0 ? (
                <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap rounded-md bg-black/20 p-2 text-[10px] text-slate-400">
                  {JSON.stringify(request.arguments, null, 2)}
                </pre>
              ) : null}
              {request.error ? <p className="mt-2 text-[11px] leading-5 text-rose-200">{request.error}</p> : null}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {retryStatuses.has(request.status) ? (
                  <button className="rounded-md bg-cyan-300/10 px-2.5 py-1 text-[11px] text-cyan-100 disabled:opacity-50" disabled={busyId === request.request_id} onClick={() => void mutate(request.request_id, "retry")} type="button">
                    重新派发
                  </button>
                ) : null}
                {waitingStatuses.has(request.status) ? (
                  <button className="rounded-md bg-rose-300/10 px-2.5 py-1 text-[11px] text-rose-100 disabled:opacity-50" disabled={busyId === request.request_id} onClick={() => void mutate(request.request_id, "cancel")} type="button">
                    取消请求
                  </button>
                ) : null}
                {request.result_metadata?.artifact_id ? (
                  <a className="rounded-md bg-violet-300/10 px-2.5 py-1 text-[11px] text-violet-100" href={`/api/runtime/client-tool-artifacts/${encodeURIComponent(request.result_metadata.artifact_id)}/download`} target="_blank" rel="noreferrer">
                    查看截图
                  </a>
                ) : null}
                {request.mutating ? <span className="text-[10px] text-amber-200/70">页面修改操作</span> : null}
              </div>
            </article>
          ))}
          <button className="text-[11px] text-cyan-200 hover:text-cyan-100" onClick={() => void refresh()} type="button">刷新客户端请求</button>
        </div>
      ) : null}
    </section>
  );
}
