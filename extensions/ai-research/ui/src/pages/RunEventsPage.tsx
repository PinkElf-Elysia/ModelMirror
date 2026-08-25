import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { ErrorNotice, LoadingRows, PageHeader } from "../components/Page";
import { formatTime } from "../components/RunTable";
import { usePolling } from "../hooks/usePolling";
import type { RunEvent } from "../types";

export function groupTimelineEvents(events: RunEvent[]): RunEvent[][] {
  return events.reduce<RunEvent[][]>((groups, event) => {
    const current = groups.at(-1);
    if (event.eventType === "run.worker_update" && current?.at(-1)?.eventType === event.eventType) {
      current.push(event);
    } else {
      groups.push([event]);
    }
    return groups;
  }, []);
}

export function RunEventsPage() {
  const { runId = "" } = useParams();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const nextSequence = useRef(0);
  const batch = usePolling(
    useCallback((signal: AbortSignal) => api.events(runId, nextSequence.current, signal), [runId]),
    2_000,
  );

  useEffect(() => {
    setEvents([]);
    nextSequence.current = 0;
  }, [runId]);

  useEffect(() => {
    if (!batch.data) return;
    nextSequence.current = Math.max(nextSequence.current, batch.data.nextSequence);
    setEvents((current) => {
      const bySequence = new Map(current.map((event) => [event.sequence, event]));
      batch.data?.items.forEach((event) => bySequence.set(event.sequence, event));
      return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
    });
  }, [batch.data]);
  const timeline = useMemo(() => groupTimelineEvents(events), [events]);

  return (
    <div className="page">
      <PageHeader eyebrow="Run events" title="持久事件时间线" description={`${runId} · 按序增量轮询，不使用临时流连接。`} actions={<Link className="button" to={`/runs/${runId}`}>返回详情</Link>} />
      <section className="section" aria-labelledby="timeline-title">
        <div className="flex items-center justify-between gap-3"><h2 className="section-title" id="timeline-title">事件</h2><span className="text-xs text-[var(--muted)]">afterSeq {nextSequence.current}</span></div>
        {events.length ? (
          <ol className="relative mt-5 border-l border-[var(--border-strong)] pl-5">
            {timeline.map((group) => {
              const event = group[0];
              const last = group.at(-1) ?? event;
              const grouped = group.length > 1;
              return (
              <li className="relative pb-7" key={event.sequence}>
                <span className="absolute -left-[25px] top-1 h-2 w-2 rounded-full bg-[var(--cyan)] ring-4 ring-[#10282a]" aria-hidden="true" />
                <div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="font-mono text-sm font-semibold text-[#dce6e9]">{grouped ? `${event.sequence}–${last.sequence}. ${event.eventType} · ${group.length} 次` : `${event.sequence}. ${event.eventType}`}</h3><time className="text-xs text-[var(--muted)]">{formatTime(last.createdAt)}</time></div>
                {grouped ? (
                  <details className="mt-3 border-l border-[var(--border)] pl-3">
                    <summary className="cursor-pointer text-xs font-semibold text-[var(--cyan)]">展开 {group.length} 条原始事件</summary>
                    <ol className="mt-3 space-y-3">
                      {group.map((item) => <li key={item.sequence}><p className="font-mono text-xs text-[var(--muted)]">#{item.sequence} · {formatTime(item.createdAt)}</p>{Object.keys(item.payload).length ? <pre className="code-block mt-2">{JSON.stringify(item.payload, null, 2)}</pre> : <p className="mt-2 text-xs text-[var(--muted)]">无附加载荷</p>}</li>)}
                    </ol>
                  </details>
                ) : Object.keys(event.payload).length ? <pre className="code-block mt-3">{JSON.stringify(event.payload, null, 2)}</pre> : <p className="mt-2 text-xs text-[var(--muted)]">无附加载荷</p>}
              </li>
              );
            })}
          </ol>
        ) : batch.loading ? <LoadingRows count={4} /> : batch.error ? <ErrorNotice message={batch.error.message} onRetry={batch.refresh} /> : <p className="my-8 text-center text-sm text-[var(--muted)]">尚无持久事件</p>}
        <p className="sr-only" aria-live="polite">已加载 {events.length} 个事件</p>
      </section>
    </div>
  );
}
