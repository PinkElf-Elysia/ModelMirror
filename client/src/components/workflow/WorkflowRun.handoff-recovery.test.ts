import { afterEach, describe, expect, it } from "vitest";
import {
  buildRunSteps,
  persistWorkflowRunRecovery,
  readWorkflowRunRecovery,
  shouldRecordNodeStreamFailure,
  workflowRunRecoveryKey,
} from "./WorkflowRun";

afterEach(() => {
  window.sessionStorage.clear();
});

describe("WorkflowRun handoff recovery pointer", () => {
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
