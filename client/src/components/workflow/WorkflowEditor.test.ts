import { describe, expect, it } from "vitest";

import { createNodeData } from "./WorkflowEditor";
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
  });
});
