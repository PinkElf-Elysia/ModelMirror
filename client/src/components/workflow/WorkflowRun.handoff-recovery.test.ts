import { afterEach, describe, expect, it } from "vitest";
import {
  buildRunSteps,
  isTerminalWorkflowRunEvent,
  persistWorkflowRunRecovery,
  readWorkflowRunRecovery,
  shouldShowHandoffInboxLink,
  shouldRecordNodeStreamFailure,
  workflowRunRecoveryKey,
} from "./WorkflowRun";

afterEach(() => {
  window.sessionStorage.clear();
});

describe("WorkflowRun handoff recovery pointer", () => {
  it("keeps node compatibility errors non-terminal unless explicitly marked", () => {
    expect(isTerminalWorkflowRunEvent({
      event: "error",
      node_id: "legacy-1",
      message: "soft error",
    })).toBe(false);
    expect(isTerminalWorkflowRunEvent({
      event: "error",
      node_id: "http-1",
      terminal: true,
      message: "terminal error",
    })).toBe(true);
  });

  it("keeps safe code and actual attempts on a recovered node-less error", () => {
    const steps = buildRunSteps([{
      event: "error",
      terminal: true,
      code: "HTTP_TIMEOUT",
      message: "HTTP request timed out.",
      attempt: 3,
      max_attempts: 3,
      classification: "transient",
      exhausted: true,
    }]);

    expect(steps).toEqual([
      expect.objectContaining({
        title: "工作流",
        status: "error",
        output: expect.stringMatching(/HTTP_TIMEOUT.*第 3\/3 次尝试.*已耗尽/),
      }),
    ]);
    expect(buildRunSteps([{
      event: "error",
      terminal: true,
      code: "unsafe secret code",
      message: "safe message",
    }])[0]?.output).not.toContain("unsafe secret code");
  });

  it("keeps scheduled and started retry attempts as structured node history", () => {
    const steps = buildRunSteps([
      {
        event: "node_retry_scheduled",
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
        attempt: 2,
        max_attempts: 3,
        resume_at: 1_780_000_005,
        error_code: "HTTP_STATUS_503",
        classification: "transient",
      },
      {
        event: "node_retry_started",
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
        attempt: 2,
        max_attempts: 3,
      },
      {
        event: "node_end",
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
        output: "request completed",
      },
    ]);

    expect(steps).toEqual([
      expect.objectContaining({
        id: "http-1",
        status: "done",
        output: "request completed",
        retryEvents: [
          expect.objectContaining({
            state: "scheduled",
            attempt: 2,
            maxAttempts: 3,
            errorCode: "HTTP_STATUS_503",
          }),
          expect.objectContaining({ state: "started", attempt: 2, maxAttempts: 3 }),
        ],
      }),
    ]);
  });

  it("deduplicates replayed retry events and reports the final actual attempt", () => {
    const scheduled = {
      event: "node_retry_scheduled" as const,
      node_id: "query-1",
      node_title: "查询数据表",
      node_type: "data_table_query" as const,
      attempt: 3,
      max_attempts: 3,
      resume_at: 1_780_000_030,
      error_code: "DATA_TABLE_BUSY",
      classification: "transient" as const,
    };
    const steps = buildRunSteps([
      scheduled,
      scheduled,
      {
        event: "node_error_routed",
        node_id: "query-1",
        node_title: "查询数据表",
        node_type: "data_table_query",
        attempt: 3,
        max_attempts: 3,
        error_code: "DATA_TABLE_BUSY",
        classification: "transient",
      },
    ]);

    expect(steps[0]).toEqual(expect.objectContaining({
      status: "handled_error",
      output: expect.stringContaining("尝试 3/3"),
      retryEvents: [expect.objectContaining({ state: "scheduled", attempt: 3 })],
    }));
  });

  it("distinguishes a handled node failure from a failed workflow", () => {
    const steps = buildRunSteps([
      {
        event: "node_error_routed",
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
        error_code: "HTTP_TIMEOUT",
        classification: "transient",
        attempt: 1,
        max_attempts: 1,
      },
      {
        event: "node_end",
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
        status: "handled_error",
      },
      { event: "workflow_end", final_output: "fallback" },
    ]);

    expect(steps).toEqual([
      expect.objectContaining({
        id: "http-1",
        status: "handled_error",
        output: expect.stringContaining("HTTP_TIMEOUT"),
      }),
    ]);
    expect(steps[0]?.output).toContain("瞬时");
  });

  it("records a node-level stream failure when no terminal event was emitted", () => {
    expect(shouldRecordNodeStreamFailure(1, false)).toBe(true);
    expect(shouldRecordNodeStreamFailure(0, false)).toBe(false);
    expect(shouldRecordNodeStreamFailure(1, true)).toBe(false);
  });

  it("joins workflow agent streaming deltas without inserting line breaks", () => {
    const steps = buildRunSteps([
      {
        event: "node_delta",
        node_id: "agent",
        node_title: "脱敏副本复述",
        node_type: "workflow_agent",
        output: "请",
      },
      {
        event: "node_delta",
        node_id: "agent",
        node_title: "脱敏副本复述",
        node_type: "workflow_agent",
        output: "原",
      },
      {
        event: "node_delta",
        node_id: "agent",
        node_title: "脱敏副本复述",
        node_type: "workflow_agent",
        output: "样",
      },
      {
        event: "node_end",
        node_id: "agent",
        node_title: "脱敏副本复述",
        node_type: "workflow_agent",
        output: "请原样",
      },
    ]);

    expect(steps).toEqual([
      expect.objectContaining({ id: "agent", output: "请原样", status: "done" }),
    ]);
  });

  it("collapses repeated knowledge proposal redaction deltas into one clear message", () => {
    const steps = buildRunSteps([
      {
        event: "node_delta",
        node_id: "agent",
        node_title: "公告草案生成",
        node_type: "workflow_agent",
        output: "knowledge proposal source withheld",
      },
      {
        event: "node_delta",
        node_id: "agent",
        node_title: "公告草案生成",
        node_type: "workflow_agent",
        output: "knowledge proposal source withheld",
      },
      {
        event: "node_end",
        node_id: "agent",
        node_title: "公告草案生成",
        node_type: "workflow_agent",
        output: "knowledge proposal source withheld",
      },
    ]);

    expect(steps).toEqual([
      expect.objectContaining({
        id: "agent",
        output: "提议正文已隐藏，仅可在 Knowledge Inbox 中查看。",
        status: "done",
      }),
    ]);
  });

  it("does not leave a completed Creator handoff as a running workflow step", () => {
    const steps = buildRunSteps([
      {
        event: "node_end",
        node_id: "agent",
        node_title: "梳理需求",
        node_type: "workflow_agent",
        output: "需求分析完成。",
      },
      {
        event: "skill_creator_handoff",
        status: "ready",
        node_id: "creator",
        session_id: "creator-1",
      },
      { event: "workflow_end", final_output: "需求分析完成。" },
    ]);

    expect(steps).toEqual([
      expect.objectContaining({ id: "agent", status: "done" }),
    ]);
    expect(steps.some((step) => step.status === "running")).toBe(false);
  });

  it("keeps a redacted managed receipt on its node and does not overwrite an error", () => {
    const receipt = {
      contract_version: "modelmirror-provider-workload-routing-v1",
      entry_id: "workflow_interactive_llm" as const,
      routing_mode: "managed_required" as const,
      run_reference: "workrun-safe",
      status: "failed" as const,
      call_count: 0,
      reason_codes: ["provider_workload_binding_missing"],
      calls: [],
    };
    const steps = buildRunSteps([
      {
        event: "error",
        node_id: "llm-1",
        node_title: "LLM",
        node_type: "llm",
        message: "发送前已阻断。",
        provider_route_receipts: receipt,
      },
      {
        event: "node_end",
        node_id: "llm-1",
        node_title: "LLM",
        node_type: "llm",
        output: "",
      },
    ]);

    expect(steps).toEqual([
      expect.objectContaining({
        id: "llm-1",
        status: "error",
        providerRouteReceipt: receipt,
      }),
    ]);
  });

  it("shows unselected branch nodes as skipped without exposing values", () => {
    const steps = buildRunSteps([
      {
        event: "node_skipped",
        node_id: "unselected",
        node_title: "未选择的外部请求",
        node_type: "http_request",
        status: "skipped",
        message: "未命中当前分支，已跳过。",
      },
    ]);

    expect(steps).toEqual([
      expect.objectContaining({
        id: "unselected",
        status: "skipped",
        output: "未命中当前分支，已跳过。",
      }),
    ]);
  });

  it("shows a durable agent handoff as waiting with only safe target metadata", () => {
    const steps = buildRunSteps([
      {
        event: "agent_handoff_waiting",
        node_id: "handoff-1",
        node_title: "移交已有任务",
        node_type: "agent_handoff",
        wait_kind: "agent_handoff",
        wait_id: "handoff_safe",
        agent_task_id: "task_safe",
        agent_handoff_id: "handoff_safe",
        target_kind: "xpert",
        target_id: "review-xpert",
        target_version: 3,
        message: "任务已移交，正在等待完成。",
      },
    ]);

    expect(steps).toEqual([
      expect.objectContaining({
        id: "handoff-1",
        status: "waiting",
        output: "任务已移交，正在等待完成。",
      }),
    ]);
    expect(JSON.stringify(steps)).not.toContain("task input");
    expect(JSON.stringify(steps)).not.toContain("reason");
    expect(shouldShowHandoffInboxLink(steps[0])).toBe(true);
  });

  it("replaces the waiting copy when a recovered handoff completes", () => {
    const steps = buildRunSteps([
      {
        event: "agent_handoff_waiting",
        node_id: "router",
        node_title: "路由并移交",
        node_type: "handoff_router",
        message: "Workflow is waiting for the delegated task to finish.",
      },
      {
        event: "node_end",
        node_id: "router",
        node_title: "路由并移交",
        node_type: "handoff_router",
        output: "协作任务已完成。",
      },
    ]);

    expect(steps).toEqual([
      expect.objectContaining({
        id: "router",
        status: "done",
        output: "协作任务已完成。",
      }),
    ]);
    expect(steps[0].output).not.toContain("waiting");
  });

  it("only offers the Inbox shortcut for actionable collaboration steps", () => {
    expect(shouldShowHandoffInboxLink({
      id: "router",
      title: "路由并移交",
      type: "handoff_router",
      status: "done",
      output: "submitted",
    })).toBe(true);
    expect(shouldShowHandoffInboxLink({
      id: "llm",
      title: "LLM",
      type: "llm",
      status: "done",
      output: "done",
    })).toBe(false);
    expect(shouldShowHandoffInboxLink({
      id: "handoff",
      title: "移交失败",
      type: "agent_handoff",
      status: "error",
      output: "failed",
    })).toBe(false);
  });

  it("stores only bounded task and run ids for page refresh recovery", () => {
    const taskId = "a".repeat(32);
    const runId = "123e4567-e89b-42d3-a456-426614174000";
    persistWorkflowRunRecovery("workflow-1", {
      taskId,
      runId,
    });

    expect(readWorkflowRunRecovery("workflow-1")).toEqual({
      taskId,
      runId,
    });
    expect(JSON.parse(
      window.sessionStorage.getItem(workflowRunRecoveryKey("workflow-1")) ?? "{}",
    )).toEqual({ taskId, runId });
  });

  it("drops malformed or overlong pointers instead of requesting arbitrary paths", () => {
    const key = workflowRunRecoveryKey("workflow-2");
    window.sessionStorage.setItem(key, "not-json");
    expect(readWorkflowRunRecovery("workflow-2")).toBeNull();
    expect(window.sessionStorage.getItem(key)).toBeNull();

    window.sessionStorage.setItem(key, JSON.stringify({
      taskId: "t".repeat(201),
      runId: "123e4567-e89b-42d3-a456-426614174000",
    }));
    expect(readWorkflowRunRecovery("workflow-2")).toBeNull();
    expect(window.sessionStorage.getItem(key)).toBeNull();
  });
});
