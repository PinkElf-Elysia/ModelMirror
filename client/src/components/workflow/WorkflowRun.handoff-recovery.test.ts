import { afterEach, describe, expect, it } from "vitest";
import {
  buildRunSteps,
  persistWorkflowRunRecovery,
  readWorkflowRunRecovery,
  workflowRunRecoveryKey,
} from "./WorkflowRun";

afterEach(() => {
  window.sessionStorage.clear();
});

describe("WorkflowRun handoff recovery pointer", () => {
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
