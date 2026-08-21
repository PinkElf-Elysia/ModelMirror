import { describe, expect, it } from "vitest";

import {
  createNodeData,
  normalizeWorkflowNodePositions,
} from "./WorkflowEditor";
import {
  knowledgePipelineItems,
  workflowPaletteSections,
} from "./workflowNodeRegistry";

describe("WorkflowEditor palette defaults", () => {
  it("provides editable defaults for every local palette item", () => {
    const items = [
      ...workflowPaletteSections.flatMap((section) => section.items),
      ...knowledgePipelineItems,
    ].filter((item) => item.enabled !== false);

    for (const item of items) {
      const data = createNodeData(item.kind);
      expect(data.kind).toBe(item.kind);
      expect(data.title).toBeTruthy();
      expect(data.description).toBeTruthy();
    }
  });

  it("keeps R1 deployment nodes planner-independent with safe defaults", () => {
    expect(createNodeData("scheduled_start")).toMatchObject({
      scheduleType: "interval",
      intervalSeconds: 30,
      timezone: "UTC",
      eventVariable: "schedule_event",
    });
    expect(createNodeData("http_event_entry")).toMatchObject({
      eventVariable: "http_event",
      bodyVariable: "request_body",
      acceptedContentType: "both",
      maxBodyBytes: 1_048_576,
    });
    expect(createNodeData("suspend_wait")).toMatchObject({
      waitMode: "duration",
      durationSeconds: 60,
      untilInputMode: "fixed",
      untilTimezone: "UTC",
      outputVariable: "resume_event",
    });
    expect(createNodeData("http_event_reply")).toMatchObject({
      statusCode: 200,
      responseBodyType: "json",
    });
    expect(createNodeData("failure_event_entry")).toMatchObject({
      sourceProjectIds: [],
      eventVariable: "failure_event",
    });
    expect(createNodeData("workflow_call_entry")).toMatchObject({
      eventVariable: "call_event",
    });
    expect(createNodeData("invoke_workflow")).toMatchObject({
      targetProjectId: "",
      targetVersion: "",
      inputBindings: {},
      resultVariable: "workflow_result",
      timeoutSeconds: 60,
    });
  });

  it("repairs missing or non-finite positions from server and legacy drafts", () => {
    const nodes = [
      {
        id: "missing-position",
        type: "workflowNode",
        position: undefined,
        data: createNodeData("failure_event_entry"),
      },
      {
        id: "invalid-position",
        type: "workflowNode",
        position: { x: Number.NaN, y: 10 },
        data: createNodeData("output"),
      },
      {
        id: "valid-position",
        type: "workflowNode",
        position: { x: 90, y: 120 },
        data: createNodeData("output"),
      },
    ] as Parameters<typeof normalizeWorkflowNodePositions>[0];

    const normalized = normalizeWorkflowNodePositions(nodes);
    expect(normalized[0].position).toEqual({ x: 0, y: 80 });
    expect(normalized[0].type).toBe("workflowNode");
    expect(normalized[1].position).toEqual({ x: 320, y: 80 });
    expect(normalized[2]).toBe(nodes[2]);
  });
});
