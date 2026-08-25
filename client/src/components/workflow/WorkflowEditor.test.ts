import { describe, expect, it } from "vitest";
import { type WorkflowNode } from "../../types/workflow";

import {
  createNodeData,
  dataMergeConnectionError,
  findAvailablePalettePosition,
  normalizeWorkflowNodePositions,
  parseSkillRuntimeIds,
  reconcileMcpArgumentBindings,
  updateSkillRuntimeIds,
  workflowTypesForMcpSchema,
} from "./WorkflowEditor";
import {
  knowledgePipelineItems,
  workflowPaletteSections,
} from "./workflowNodeRegistry";

describe("WorkflowEditor palette defaults", () => {
  it("places palette-click nodes at the nearest available canvas position", () => {
    const preferred = { x: 320, y: 80 };
    const centeredNode = {
      id: "existing-center",
      type: "workflowNode",
      position: { ...preferred },
      measured: { width: 144, height: 96 },
      data: createNodeData("workflow_agent"),
    } as WorkflowNode;

    expect(findAvailablePalettePosition(preferred, [])).toEqual(preferred);
    expect(findAvailablePalettePosition(preferred, [centeredNode])).toEqual({
      x: 320,
      y: 216,
    });
    expect(centeredNode.position).toEqual(preferred);
  });

  it("keeps subsequent palette-click nodes from sharing the same fallback slot", () => {
    const preferred = { x: 320, y: 80 };
    const occupiedPositions = [
      preferred,
      { x: 320, y: 216 },
    ].map((position, index) => ({
      id: `existing-${index}`,
      type: "workflowNode",
      position,
      measured: { width: 144, height: 96 },
      data: createNodeData("workflow_agent"),
    })) as WorkflowNode[];

    expect(findAvailablePalettePosition(preferred, occupiedPositions)).toEqual({
      x: 512,
      y: 80,
    });
  });

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
    expect(createNodeData("data_merge")).toMatchObject({
      contractVersion: 1,
      mergeMode: "append",
      leftVariable: "left_rows",
      rightVariable: "right_rows",
      outputVariable: "merged_rows",
      keyFields: [],
    });
  });

  it("rejects missing or duplicate data merge target handles before connecting", () => {
    expect(dataMergeConnectionError("data_merge", "merge-1", null, [])).toMatch(/左侧数据/);
    expect(dataMergeConnectionError("data_merge", "merge-1", "left", [
      {
        id: "existing-left",
        source: "source-1",
        target: "merge-1",
        targetHandle: "left",
      },
    ])).toBe("数据合流的左侧入口只能连接一次。");
    expect(dataMergeConnectionError("data_merge", "merge-1", "right", [])).toBeNull();
    expect(dataMergeConnectionError("output", "output-1", "left", [])).toBe(
      "左右数据入口只属于数据合流节点。",
    );
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

  it("provides R2.0 V2 defaults without persisting MCP session identity", () => {
    expect(createNodeData("variable_assign")).toMatchObject({
      contractVersion: 2,
      outputVariable: "assigned_value",
      valueSource: "template",
      template: "收到：{{user_input}}",
    });
    expect(createNodeData("human_intervention")).toMatchObject({
      contractVersion: 2,
      interactionMode: "input",
      outputVariable: "human_input",
      timeoutSeconds: 3600,
    });
    const mcp = createNodeData("mcp_tool");
    expect(mcp).toMatchObject({
      contractVersion: 2,
      serverId: "",
      toolName: "",
      inputSchemaChecksum: "",
      argumentMode: "fields",
      argumentBindings: [],
      outputVariable: "mcp_output",
    });
    expect(mcp).not.toHaveProperty("sessionId");
    expect(mcp).not.toHaveProperty("argumentsJson");
  });

  it("creates variable pack as a typed V2 object builder", () => {
    expect(createNodeData("variable_aggregator")).toMatchObject({
      kind: "variable_aggregator",
      title: "变量打包",
      contractVersion: 2,
      outputVariable: "packed_variables",
      bindings: [
        {
          id: "binding_1",
          sourceVariable: "user_input",
          outputField: "user_input",
        },
      ],
    });
  });

  it("creates Code as safe text V2 and keeps the retired template out of new-node sources", () => {
    const code = createNodeData("code");
    expect(code).toMatchObject({
      kind: "code",
      title: "安全文本加工",
      contractVersion: 2,
      operation: "upper",
      inputVariable: "user_input",
      outputVariable: "code_output",
    });
    expect(code).not.toHaveProperty("codeOperation");
    expect(code).not.toHaveProperty("pythonCode");

    const paletteKinds = workflowPaletteSections.flatMap((section) =>
      section.items.map((item) => item.kind),
    );
    expect(paletteKinds).not.toContain("template_transform");
  });

  it("maps MCP JSON Schema field types to workflow variable types", () => {
    expect(workflowTypesForMcpSchema({ type: "string" })).toEqual(["text"]);
    expect(workflowTypesForMcpSchema({ type: "integer" })).toEqual(["number"]);
    expect(workflowTypesForMcpSchema({ type: "array" })).toEqual(["json"]);
    expect(
      workflowTypesForMcpSchema({ anyOf: [{ type: "string" }, { type: "null" }] }),
    ).toEqual(["text", "json"]);
    expect(workflowTypesForMcpSchema({})).toEqual(["unknown"]);
  });

  it("reconciles MCP schema drift without losing same-name bindings", () => {
    const reconciled = reconcileMcpArgumentBindings(
      {
        type: "object",
        properties: {
          scope: { type: "string" },
          query: { type: "string" },
        },
      },
      [
        {
          id: "argument_1",
          name: "query",
          binding: { source: "variable", variable: "user_input" },
        },
      ],
    );

    expect(reconciled).toEqual({
      argumentMode: "fields",
      argumentBindings: [
        {
          id: "argument_2",
          name: "scope",
          binding: { source: "literal", value: "" },
        },
        {
          id: "argument_1",
          name: "query",
          binding: { source: "variable", variable: "user_input" },
        },
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
