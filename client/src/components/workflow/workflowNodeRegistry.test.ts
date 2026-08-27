import { describe, expect, it } from "vitest";

import {
  hasNodeContractV3,
  workflowNodeRegistryFallback,
} from "./workflowNodeRegistry";

describe("workflowNodeRegistry NodeContract V3 guard", () => {
  it("keeps fallback presentation-only and planner-neutral", () => {
    expect(hasNodeContractV3(workflowNodeRegistryFallback)).toBe(false);

    const items = [
      ...workflowNodeRegistryFallback.sections.flatMap((section) => section.items),
      ...workflowNodeRegistryFallback.knowledge_pipeline.items,
    ];
    const kinds = items.map((item) => item.kind);

    expect(new Set(kinds).size).toBe(kinds.length);
    expect(kinds).toEqual(expect.arrayContaining([
      "json_serialize",
      "json_deserialize",
      "data_table_query",
      "data_table_insert",
      "data_table_update",
      "data_table_delete",
      "annotation",
      "knowledge_base",
      "knowledge_retrieval",
      "vision_understanding",
      "knowledge_write_proposal",
      "scheduled_start",
      "http_event_entry",
      "form_event_entry",
      "failure_event_entry",
      "workflow_call_entry",
      "invoke_workflow",
      "suspend_wait",
      "http_event_reply",
      "terminate_error",
      "multi_route",
      "data_aggregate",
      "data_merge",
      "dataset_compare",
      "object_transform",
      "file_output",
    ]));
    expect(items.every((item) => item.contract === undefined)).toBe(true);
    expect(items.every((item) => item.planner === undefined)).toBe(true);
    expect(items.every((item) => item.enabled === false)).toBe(true);
    expect(kinds).not.toContain("template_transform");
    expect(items.find((item) => item.kind === "code")).toMatchObject({
      title: "安全文本加工",
      tags: ["text", "safe", "transform"],
    });
    expect(
      items.every(
        (item) =>
          item.metadata?.status_reason ===
          "节点注册表不可用，无法确认当前执行契约。",
      ),
    ).toBe(true);
  });

  it("rejects a nominal V3 registry without per-node contracts", () => {
    expect(hasNodeContractV3({
      ...workflowNodeRegistryFallback,
      version: "xpert-workflow-node-registry-v4",
      contract_version: 3,
      contract_checksum: "registry-checksum",
    })).toBe(false);
  });

  it("rejects an empty registry even with V3 metadata", () => {
    expect(hasNodeContractV3({
      version: "xpert-workflow-node-registry-v4",
      contract_version: 3,
      contract_checksum: "registry-checksum",
      tabs: [],
      sections: [],
      knowledge_pipeline: { items: [], placeholders: [] },
    })).toBe(false);
  });
});
