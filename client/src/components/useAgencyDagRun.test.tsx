import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAgencyDagRun } from "./useAgencyDagRun";
import type { AgencyDagRun } from "./AgencyExpertTeamTypes";

class EventSourceStub {
  static instances: EventSourceStub[] = [];
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();

  constructor(readonly url: string) {
    EventSourceStub.instances.push(this);
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
  }
}

function run(overrides: Partial<AgencyDagRun> = {}): AgencyDagRun {
  return {
    task_id: "agency_dag_source",
    run_id: "run-source",
    model_id: "deepseek/deepseek-chat",
    status: "completed",
    sequence: 3,
    events: [],
    steps: [],
    final_output: "第一版",
    warnings: [],
    model_calls: 3,
    usage: { input_tokens: 30, output_tokens: 12 },
    revisable: true,
    created_at: 1,
    updated_at: 2,
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

beforeEach(() => {
  EventSourceStub.instances = [];
  vi.stubGlobal("EventSource", EventSourceStub);
  window.localStorage.clear();
  window.history.replaceState({}, "", "/expert-team");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useAgencyDagRun revision navigation", () => {
  it("moves to the immutable revision task and resumes its SSE stream", async () => {
    const source = run();
    const revision = run({
      task_id: "agency_dag_revision",
      run_id: "run-revision",
      status: "running",
      sequence: 0,
      final_output: null,
      model_calls: 0,
      usage: {},
      revision: {
        parent_task_id: source.task_id,
        root_task_id: source.task_id,
        revision_index: 1,
        target_task_id: "implementation_plan",
        feedback: "请收紧预算并标注待确认项。",
        affected_task_ids: ["implementation_plan", "final_report"],
      },
      lineage_model_calls: 3,
      lineage_usage: source.usage,
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/dag-runs/${source.task_id}`) && !init?.method) {
        return jsonResponse(source);
      }
      if (url.endsWith(`/dag-runs/${source.task_id}/revise`) && init?.method === "POST") {
        return jsonResponse(revision, 202);
      }
      return jsonResponse({ error: `unexpected request: ${url}` }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    const hook = renderHook(() => useAgencyDagRun());

    await act(async () => {
      await hook.result.current.restore(source.task_id);
    });
    expect(hook.result.current.run?.task_id).toBe(source.task_id);

    const feedback = "请收紧预算并标注待确认项。";
    await act(async () => {
      await hook.result.current.revise({
        target_task_id: "implementation_plan",
        feedback,
      });
    });
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/expert-team/dag-runs/${source.task_id}/revise`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          target_task_id: "implementation_plan",
          feedback,
        }),
      }),
    );
    expect(hook.result.current.run?.revision?.parent_task_id).toBe(source.task_id);
    expect(new URLSearchParams(window.location.search).get("dag_task")).toBe(
      revision.task_id,
    );
    expect(JSON.parse(window.localStorage.getItem(
      "modelmirror-expert-team-agency-recent-runs",
    ) || "[]")[0]).toBe(revision.task_id);
    expect(EventSourceStub.instances).toHaveLength(1);
    expect(EventSourceStub.instances[0].url).toContain(
      `/api/expert-team/dag-runs/${revision.task_id}/events?after_sequence=0`,
    );

    act(() => {
      EventSourceStub.instances[0].emit({
        event: "agency.run.completed",
        sequence: 1,
        status: "completed",
        final_output: "修订完成",
        model_calls: 2,
        usage: { input_tokens: 10, output_tokens: 5 },
      });
    });
    await waitFor(() => expect(hook.result.current.run?.status).toBe("completed"));
    expect(hook.result.current.run?.final_output).toBe("修订完成");
    expect(hook.result.current.run?.revision?.revision_index).toBe(1);
    expect(hook.result.current.run?.lineage_model_calls).toBe(5);
    expect(hook.result.current.run?.lineage_usage).toEqual({
      input_tokens: 40,
      output_tokens: 17,
    });
  });
});
