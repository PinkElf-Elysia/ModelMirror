import { describe, expect, it } from "vitest";

import {
  createNodeData,
  normalizeWorkflowNodePositions,
  parseSkillRuntimeIds,
  updateSkillRuntimeIds,
} from "./WorkflowEditor";
import {
  knowledgePipelineItems,
  workflowPaletteSections,
} from "./workflowNodeRegistry";

describe("WorkflowEditor palette defaults", () => {
  it("normalizes the tag-based required Skill editor without duplicate IDs", () => {
    expect(parseSkillRuntimeIds("pdf, tdd\npdf")).toEqual(["pdf", "tdd"]);
    expect(updateSkillRuntimeIds("pdf", "tdd", "add")).toBe("pdf, tdd");
    expect(updateSkillRuntimeIds("pdf, tdd", "pdf", "remove")).toBe("tdd");
  });

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

  it("creates newly dragged Skill Creator middleware in V2 handoff mode", () => {
    const data = createNodeData("runtime_middleware", {
      kind: "runtime_middleware",
      runtimeMiddlewareId: "skill_creator",
      runtimeMiddlewareKind: "runtime_middleware.skill_creator",
      title: "Skill 创建器",
      description: "完成需求分析后创建 Creator 会话。",
      metadata: {},
      fields: [
        {
          name: "authoring_mode",
          label: "创建方式",
          type: "select",
          default: "creator_handoff",
        },
        {
          name: "allow_create",
          label: "允许创建",
          type: "boolean",
          default: true,
        },
      ],
    });

    expect(data.runtimeMiddlewareConfig).toEqual({
      authoring_mode: "creator_handoff",
    });
  });

  it("creates newly dragged Skill Hook middleware in typed V2 mode", () => {
    const data = createNodeData("runtime_middleware", {
      kind: "runtime_middleware",
      runtimeMiddlewareId: "plugin_hooks",
      runtimeMiddlewareKind: "runtime_middleware.plugin_hooks",
      title: "Skill 插件 Hook",
      description: "在固定事件边界执行已安装 Skill 的类型化 Hook。",
      metadata: {},
      fields: [
        {
          name: "hook_mode",
          label: "Hook 合同",
          type: "select",
          default: "typed_v2",
        },
        {
          name: "skill_ids",
          label: "Hook Skill",
          type: "textarea",
          default: "",
        },
        {
          name: "fail_closed",
          label: "Legacy 失败策略",
          type: "boolean",
          default: false,
        },
      ],
    });

    expect(data.runtimeMiddlewareConfig).toEqual({
      hook_mode: "typed_v2",
      skill_ids: "",
    });
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

  it("provides safe structured defaults for R1.6 control and data nodes", () => {
    expect(createNodeData("terminate_error")).toMatchObject({
      errorCode: "WORKFLOW_STOPPED",
      message: "工作流已按规则主动终止。",
    });
    expect(createNodeData("multi_route")).toMatchObject({
      inputVariable: "user_input",
      routes: [
        { id: "route_1", operator: "equals", valueType: "text" },
        { id: "route_2", operator: "equals", valueType: "text" },
      ],
    });
    expect(createNodeData("list_operation")).toMatchObject({
      operator: "length",
      filterMode: "all",
      sortKeys: [{ field: "", direction: "asc", nulls: "last" }],
      deduplicateFields: [],
    });
    expect(createNodeData("data_aggregate")).toMatchObject({
      inputVariable: "user_input",
      outputVariable: "aggregate_result",
      groupByFields: [],
      measures: [{ outputField: "row_count", operation: "count" }],
    });
  });

  it("provides safe structured defaults for R1.7 HTTP, condition, and dataset nodes", () => {
    expect(createNodeData("http_request")).toMatchObject({
      contractVersion: 2,
      method: "GET",
      url: "https://example.com",
      queryItems: [],
      headerItems: [],
      bodyMode: "none",
      authType: "none",
      timeoutSeconds: 30,
      redirectLimit: 0,
      responseLimitBytes: 1_048_576,
      statusPolicy: "success_only",
      outputVariable: "http_response",
    });
    expect(createNodeData("condition")).toMatchObject({
      contractVersion: 2,
      inputVariable: "user_input",
      field: "",
      operator: "contains",
      valueType: "text",
    });
    expect(createNodeData("dataset_compare")).toMatchObject({
      leftVariable: "before_rows",
      rightVariable: "after_rows",
      keyFields: ["id"],
      includeUnchanged: false,
      outputVariable: "dataset_difference",
    });
  });

  it("provides safe structured defaults for R1.8 file and deterministic data nodes", () => {
    expect(createNodeData("document_extractor")).toMatchObject({
      contractVersion: 2,
      assetIdVariable: "selected_file_asset_id",
      outputVariable: "document_text",
    });
    expect(createNodeData("time_tool")).toMatchObject({
      contractVersion: 2,
      operation: "now",
      timezone: "UTC",
      amount: 1,
      unit: "days",
      outputVariable: "current_time",
    });
    expect(createNodeData("object_transform")).toMatchObject({
      inputVariable: "source_object",
      outputVariable: "transformed_object",
      operations: [
        expect.objectContaining({
          id: "operation_1",
          operation: "set_default",
          targetField: "status",
        }),
      ],
    });
    expect(createNodeData("file_output")).toMatchObject({
      inputVariable: "report_content",
      outputVariable: "generated_file",
      format: "markdown",
      filenameTemplate: "workflow-report",
    });
    expect(createNodeData("list_operation")).toMatchObject({
      count: 10,
      startIndex: 0,
      endIndex: 10,
    });
  });

  it("provides strict V2 defaults for typed AI nodes", () => {
    expect(createNodeData("parameter_extractor")).toMatchObject({
      contractVersion: 2,
      schemaMode: "fields",
      outputShape: "object",
      repairAttempts: 0,
      outputVariable: "parameters",
      fields: [
        expect.objectContaining({ id: "field_1", valueType: "string" }),
        expect.objectContaining({ id: "field_2", nullable: true }),
      ],
    });
    expect(createNodeData("question_classifier")).toMatchObject({
      contractVersion: 2,
      classificationMode: "rules_only",
      defaultLabel: "未分类",
      caseSensitive: false,
      categoriesV2: [
        expect.objectContaining({ id: "category_1" }),
        expect.objectContaining({ id: "category_2" }),
      ],
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
