import type { DataXResult } from "../../types/datax";

function parseResult(content: string): DataXResult | null {
  const trimmed = content.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    const value = JSON.parse(trimmed) as Partial<DataXResult>;
    if (
      typeof value.artifact_id !== "string" ||
      typeof value.project_id !== "string" ||
      typeof value.model_id !== "string" ||
      !Array.isArray(value.columns) ||
      !Array.isArray(value.rows) ||
      !["kpi", "table", "line", "bar"].includes(String(value.view))
    ) return null;
    return value as DataXResult;
  } catch {
    return null;
  }
}

export default function DataXResultCard({ content }: { content: string }) {
  const result = parseResult(content);
  if (!result) return null;
  const metric = result.columns[result.columns.length - 1] || "value";
  return (
    <section className="mt-2 overflow-hidden rounded-md border border-cyan-300/20 bg-cyan-300/[0.055]">
      <header className="flex items-center justify-between gap-3 border-b border-cyan-300/15 px-3 py-2">
        <div><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-cyan-200">Data X result</p><p className="mt-0.5 text-xs text-slate-400">{result.view} · {result.row_count} 行{result.truncated ? " · 已截断" : ""}</p></div>
        <span className="font-mono text-[9px] text-slate-600">{result.artifact_id}</span>
      </header>
      {result.view === "kpi" ? (
        <div className="px-3 py-5"><p className="text-xs text-slate-500">{metric}</p><p className="mt-1 text-3xl font-semibold tabular-nums text-white">{String(result.rows[0]?.[metric] ?? "-")}</p></div>
      ) : (
        <div className="max-h-64 overflow-auto"><table className="w-full min-w-[420px] text-left text-[11px]"><thead className="sticky top-0 bg-ink-950/95 text-slate-400"><tr>{result.columns.map((column) => <th className="px-3 py-2 font-semibold" key={column}>{column}</th>)}</tr></thead><tbody className="divide-y divide-white/5">{result.rows.slice(0, 50).map((row, index) => <tr key={index}>{result.columns.map((column) => <td className="px-3 py-2 text-slate-300" key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>
      )}
      {result.warnings?.length ? <p className="border-t border-white/10 px-3 py-2 text-[10px] text-amber-200">{result.warnings.join(" · ")}</p> : null}
    </section>
  );
}
