import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";

import type { Run } from "../types";
import { Status, statusLabel } from "./Status";

export function formatTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

export function RunTable({ runs, empty = "暂无运行记录" }: { runs: Run[]; empty?: string }) {
  if (runs.length === 0) {
    return <p className="my-8 text-center text-sm text-[var(--muted)]">{empty}</p>;
  }

  return (
    <>
      <div className="desktop-table overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">运行</th>
              <th scope="col">夹具</th>
              <th scope="col">阶段</th>
              <th scope="col">结果</th>
              <th scope="col">证据</th>
              <th scope="col">创建时间</th>
              <th scope="col"><span className="sr-only">操作</span></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.runId}>
                <td className="font-mono text-xs text-[#d5dde1]">{run.runId}</td>
                <td>{run.caseId}</td>
                <td><Status value={run.phase} /></td>
                <td><Status value={run.outcome} /></td>
                <td><Status value={run.evidenceState} /></td>
                <td className="whitespace-nowrap text-[var(--muted)]">{formatTime(run.createdAt)}</td>
                <td>
                  <Link className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--cyan)] hover:underline" to={`/runs/${run.runId}`}>
                    查看 <ArrowUpRight aria-hidden="true" size={13} />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ol className="record-list" aria-label="运行记录">
        {runs.map((run) => (
          <li className="record-item" key={run.runId}>
            <div className="flex items-start justify-between gap-3">
              <span className="break-all font-mono text-xs text-[#dce3e7]">{run.runId}</span>
              <Link className="shrink-0 text-xs font-semibold text-[var(--cyan)]" to={`/runs/${run.runId}`}>
                查看
              </Link>
            </div>
            <div className="flex flex-wrap gap-2"><Status value={run.phase} /><Status value={run.outcome} /></div>
            <span className="text-xs text-[var(--muted)]">
              {run.caseId} · {statusLabel(run.evidenceState)} · {formatTime(run.createdAt)}
            </span>
          </li>
        ))}
      </ol>
    </>
  );
}
