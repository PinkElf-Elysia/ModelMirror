import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AgencyDagEvent,
  AgencyDagRun,
  AgencyDagRevisionPayload,
  AgencyDagStartPayload,
  AgencyDagStatus,
} from "./AgencyExpertTeamTypes";

const recentRunsKey = "modelmirror-expert-team-agency-recent-runs";
const terminalStatuses = new Set<AgencyDagStatus>([
  "completed",
  "failed",
  "cancelled",
]);

class AgencyDagApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "AgencyDagApiError";
  }
}

async function responseJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) {
    throw new AgencyDagApiError(
      payload.error || `请求失败（${response.status}）`,
      response.status,
    );
  }
  return payload;
}

function forgetTask(taskId: string) {
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(recentRunsKey) || "[]",
    ) as unknown;
    const recent = Array.isArray(parsed)
      ? parsed.filter(
          (item): item is string => typeof item === "string" && item !== taskId,
        )
      : [];
    window.localStorage.setItem(recentRunsKey, JSON.stringify(recent));
  } catch {
    window.localStorage.removeItem(recentRunsKey);
  }
  const url = new URL(window.location.href);
  if (url.searchParams.get("dag_task") === taskId) {
    url.searchParams.delete("dag_task");
    window.history.replaceState(window.history.state, "", url);
  }
}

function persistRecentTask(taskId: string) {
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(recentRunsKey) || "[]",
    ) as unknown;
    const recent = Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
    window.localStorage.setItem(
      recentRunsKey,
      JSON.stringify([taskId, ...recent.filter((item) => item !== taskId)].slice(0, 8)),
    );
  } catch {
    window.localStorage.setItem(recentRunsKey, JSON.stringify([taskId]));
  }
}

function recentTaskFromBrowser() {
  const queryTask = new URLSearchParams(window.location.search).get("dag_task");
  if (queryTask) return queryTask;
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(recentRunsKey) || "[]",
    ) as unknown;
    return Array.isArray(parsed) && typeof parsed[0] === "string" ? parsed[0] : "";
  } catch {
    return "";
  }
}

function writeTaskToUrl(taskId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("desk", "team");
  url.searchParams.set("dag_task", taskId);
  window.history.replaceState(window.history.state, "", url);
}

function mergeEvent(run: AgencyDagRun, event: AgencyDagEvent): AgencyDagRun {
  if (event.sequence <= run.sequence) return run;
  const nextSteps = [...run.steps];
  if (event.task_id && event.event.startsWith("agency.step.")) {
    const index = nextSteps.findIndex((item) => item.task_id === event.task_id);
    if (index >= 0) nextSteps[index] = event;
    else nextSteps.push(event);
  }
  let status = run.status;
  if (event.event === "agency.run.completed") status = "completed";
  if (event.event === "agency.run.failed") status = "failed";
  if (event.event === "agency.run.cancelled") status = "cancelled";
  const modelCalls = event.model_calls ?? run.model_calls;
  const usage = event.cumulative_usage ?? (
    event.event.startsWith("agency.run.") ? event.usage ?? run.usage : run.usage
  );
  const lineageCallsBefore = Math.max(
    0,
    (run.lineage_model_calls ?? run.model_calls) - run.model_calls,
  );
  const lineageInputBefore = Math.max(
    0,
    (run.lineage_usage?.input_tokens ?? run.usage.input_tokens ?? 0)
      - (run.usage.input_tokens ?? 0),
  );
  const lineageOutputBefore = Math.max(
    0,
    (run.lineage_usage?.output_tokens ?? run.usage.output_tokens ?? 0)
      - (run.usage.output_tokens ?? 0),
  );
  return {
    ...run,
    status,
    sequence: event.sequence,
    events: [...run.events, event],
    steps: nextSteps,
    final_output: event.final_output ?? run.final_output,
    quality_status: event.quality_status ?? run.quality_status,
    warnings: event.warnings ?? run.warnings,
    model_calls: modelCalls,
    usage,
    lineage_model_calls: run.lineage_model_calls === undefined
      ? undefined
      : lineageCallsBefore + modelCalls,
    lineage_usage: run.lineage_usage === undefined
      ? undefined
      : {
          input_tokens: lineageInputBefore + (usage.input_tokens ?? 0),
          output_tokens: lineageOutputBefore + (usage.output_tokens ?? 0),
        },
    error_code:
      event.event === "agency.run.failed" ? event.error || run.error_code : run.error_code,
    error_message:
      event.event === "agency.run.failed"
        ? event.message || event.error || run.error_message
        : run.error_message,
  };
}

export function useAgencyDagRun() {
  const [run, setRun] = useState<AgencyDagRun | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const runRef = useRef<AgencyDagRun | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  const setCurrentRun = useCallback((value: AgencyDagRun | null) => {
    runRef.current = value;
    setRun(value);
  }, []);

  const closeEvents = useCallback(() => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    if (reconnectRef.current !== null) {
      window.clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
  }, []);

  const refresh = useCallback(
    async (taskId: string) => {
      const response = await fetch(`/api/expert-team/dag-runs/${taskId}`);
      const payload = await responseJson<AgencyDagRun>(response);
      if (mountedRef.current) setCurrentRun(payload);
      return payload;
    },
    [setCurrentRun],
  );

  const connectEvents = useCallback(
    (taskId: string) => {
      closeEvents();
      const afterSequence = runRef.current?.task_id === taskId
        ? runRef.current.sequence
        : 0;
      const source = new EventSource(
        `/api/expert-team/dag-runs/${taskId}/events?after_sequence=${afterSequence}`,
      );
      eventSourceRef.current = source;
      source.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as AgencyDagEvent;
          if (!event || typeof event.event !== "string" || typeof event.sequence !== "number") {
            return;
          }
          setRun((current) => {
            if (!current || current.task_id !== taskId) return current;
            const next = mergeEvent(current, event);
            runRef.current = next;
            return next;
          });
        } catch {
          setError("收到无法解析的 DAG 事件，请刷新状态。");
        }
      };
      source.onerror = () => {
        source.close();
        if (!mountedRef.current) return;
        void refresh(taskId)
          .then((latest) => {
            if (!terminalStatuses.has(latest.status) && mountedRef.current) {
              reconnectRef.current = window.setTimeout(
                () => connectEvents(taskId),
                1000,
              );
            }
          })
          .catch((caught: unknown) => {
            setError(caught instanceof Error ? caught.message : "无法恢复 DAG 运行状态。");
          });
      };
    },
    [closeEvents, refresh],
  );

  const restore = useCallback(
    async (taskId: string) => {
      if (!taskId) return;
      setError("");
      try {
        const latest = await refresh(taskId);
        persistRecentTask(taskId);
        writeTaskToUrl(taskId);
        if (!terminalStatuses.has(latest.status)) connectEvents(taskId);
      } catch (caught) {
        if (caught instanceof AgencyDagApiError && caught.status === 404) {
          forgetTask(taskId);
          setCurrentRun(null);
          setError("");
          return;
        }
        setError(caught instanceof Error ? caught.message : "无法恢复最近 DAG 任务。");
      }
    },
    [connectEvents, refresh],
  );

  useEffect(() => {
    mountedRef.current = true;
    const taskId = recentTaskFromBrowser();
    if (taskId) void restore(taskId);
    return () => {
      mountedRef.current = false;
      closeEvents();
    };
  }, [closeEvents, restore]);

  const start = useCallback(
    async (payload: AgencyDagStartPayload) => {
      setBusy(true);
      setError("");
      closeEvents();
      try {
        const response = await fetch("/api/expert-team/dag-runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const created = await responseJson<AgencyDagRun>(response);
        setCurrentRun(created);
        persistRecentTask(created.task_id);
        writeTaskToUrl(created.task_id);
        connectEvents(created.task_id);
        return created;
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "DAG 启动失败。");
        throw caught;
      } finally {
        setBusy(false);
      }
    },
    [closeEvents, connectEvents, setCurrentRun],
  );

  const cancel = useCallback(async () => {
    const taskId = runRef.current?.task_id;
    if (!taskId) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(
        `/api/expert-team/dag-runs/${taskId}/cancel`,
        { method: "POST" },
      );
      const payload = await responseJson<AgencyDagRun>(response);
      closeEvents();
      setCurrentRun(payload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "取消 DAG 失败。");
    } finally {
      setBusy(false);
    }
  }, [closeEvents, setCurrentRun]);

  const retry = useCallback(async () => {
    const taskId = runRef.current?.task_id;
    if (!taskId) return;
    setBusy(true);
    setError("");
    closeEvents();
    try {
      const response = await fetch(
        `/api/expert-team/dag-runs/${taskId}/retry`,
        { method: "POST" },
      );
      const created = await responseJson<AgencyDagRun>(response);
      setCurrentRun(created);
      persistRecentTask(created.task_id);
      writeTaskToUrl(created.task_id);
      connectEvents(created.task_id);
      return created;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "DAG 续跑失败。");
      throw caught;
    } finally {
      setBusy(false);
    }
  }, [closeEvents, connectEvents, setCurrentRun]);

  const revise = useCallback(async (payload: AgencyDagRevisionPayload) => {
    const taskId = runRef.current?.task_id;
    if (!taskId) return;
    setBusy(true);
    setError("");
    closeEvents();
    try {
      const response = await fetch(
        `/api/expert-team/dag-runs/${taskId}/revise`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      const created = await responseJson<AgencyDagRun>(response);
      setCurrentRun(created);
      persistRecentTask(created.task_id);
      writeTaskToUrl(created.task_id);
      connectEvents(created.task_id);
      return created;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "DAG 返工启动失败。");
      throw caught;
    } finally {
      setBusy(false);
    }
  }, [closeEvents, connectEvents, setCurrentRun]);

  const clear = useCallback(() => {
    const taskId = runRef.current?.task_id || recentTaskFromBrowser();
    closeEvents();
    setCurrentRun(null);
    setError("");
    if (taskId) forgetTask(taskId);
  }, [closeEvents, setCurrentRun]);

  return { run, error, busy, start, retry, revise, cancel, restore, refresh, clear };
}
