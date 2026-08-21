import { useCallback, useEffect, useMemo, useState } from "react";
import { LoaderCircle, RefreshCw } from "lucide-react";

interface Connection {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  health: string;
  model_count: number;
}

interface Offering {
  connection_id: string;
  connection_name: string;
  model_id: string;
  operation: string;
  inventory_status: string;
  verification_status: string;
  invocable: boolean;
  stale: boolean;
}

async function readError(response: Response) {
  try {
    const payload = await response.json();
    return payload?.detail?.message ?? payload?.detail ?? "目录操作未完成。";
  } catch {
    return "目录操作未完成。";
  }
}

export default function ProviderCatalogPanel({ csrfToken }: { csrfToken: string }) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [offerings, setOfferings] = useState<Offering[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [connectionsResponse, offeringsResponse] = await Promise.all([
        fetch("/api/router/connections"),
        fetch("/api/router/catalog/offerings?limit=100"),
      ]);
      if (!connectionsResponse.ok) throw new Error(await readError(connectionsResponse));
      if (!offeringsResponse.ok) throw new Error(await readError(offeringsResponse));
      setConnections((await connectionsResponse.json()) as Connection[]);
      setOfferings(((await offeringsResponse.json()) as { offerings?: Offering[] }).offerings ?? []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 Provider Catalog。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const offeringsByConnection = useMemo(() => {
    const grouped = new Map<string, Offering[]>();
    for (const item of offerings) grouped.set(item.connection_id, [...(grouped.get(item.connection_id) ?? []), item]);
    return grouped;
  }, [offerings]);

  const refresh = useCallback(async (connection: Connection) => {
    setBusyId(connection.id);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/router/connections/${encodeURIComponent(connection.id)}/catalog/refresh`, {
        method: "POST",
        headers: { "X-ModelMirror-CSRF": csrfToken },
      });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json() as { model_count?: number; truncated?: boolean };
      setNotice(`${connection.name} 已发现 ${payload.model_count ?? 0} 个模型${payload.truncated ? "；结果已截断，旧目录未退休" : ""}。该检查不会发起 Chat。`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "目录刷新失败。");
    } finally {
      setBusyId(null);
    }
  }, [csrfToken, load]);

  return (
    <section className="mb-6 overflow-hidden rounded-lg border border-white/10 bg-ink-950/82 shadow-prism">
      <div className="border-b border-white/10 bg-white/[0.035] px-5 py-4">
        <h2 className="text-xl font-semibold text-white">Provider Inventory</h2>
        <p className="mt-1 text-sm text-slate-400">显式读取模型目录并生成运行时 Inventory；不会认证模型、改变路由或产生 Chat 费用。</p>
      </div>
      <div className="space-y-4 p-5">
        {error ? <p className="rounded-lg border border-rose-300/20 bg-rose-300/10 p-3 text-sm text-rose-100" role="alert">{error}</p> : null}
        {notice ? <p className="rounded-lg border border-emerald-300/20 bg-emerald-300/10 p-3 text-sm text-emerald-100" role="status">{notice}</p> : null}
        {loading ? <p className="text-sm text-slate-300"><LoaderCircle className="mr-2 inline h-4 w-4 animate-spin" />正在读取 Inventory…</p> : connections.map((connection) => {
          const rows = offeringsByConnection.get(connection.id) ?? [];
          return (
            <article className="rounded-lg border border-white/10 bg-white/[0.025] p-4" key={connection.id}>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="font-semibold text-white">{connection.name}</p>
                  <p className="mt-1 text-xs text-slate-400">{connection.kind} · {connection.health} · {rows.length || connection.model_count} 条模型证据</p>
                </div>
                <button className="inline-flex items-center justify-center gap-2 rounded-full border border-cyan-300/25 bg-cyan-300/10 px-4 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-45" disabled={busyId !== null} onClick={() => void refresh(connection)} type="button">
                  <RefreshCw className={`h-4 w-4 ${busyId === connection.id ? "animate-spin" : ""}`} />
                  {busyId === connection.id ? "正在刷新" : "刷新目录"}
                </button>
              </div>
              {rows.length ? (
                <div className="mt-4 flex flex-wrap gap-2">
                  {rows.slice(0, 12).map((row) => <span className="rounded-full border border-white/10 bg-white/[0.045] px-3 py-1 text-xs text-slate-300" key={`${row.model_id}:${row.operation}`}>{row.model_id} · {row.operation} · {row.invocable ? "可调用" : row.verification_status}</span>)}
                  {rows.length > 12 ? <span className="px-2 py-1 text-xs text-slate-500">另有 {rows.length - 12} 条</span> : null}
                </div>
              ) : <p className="mt-3 text-sm text-slate-400">尚无 v14 Inventory；请人工刷新。</p>}
            </article>
          );
        })}
        {!loading && connections.length === 0 ? <p className="text-sm text-slate-400">请先在上方创建 Provider 连接。</p> : null}
      </div>
    </section>
  );
}
