import { describe, expect, it } from "vitest";

import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import {
  analyzeWorkflowVariables,
  getWorkflowVariableFieldDescriptor,
  planWorkflowVariableRename,
  resolveWorkflowVariableFieldTypes,
  validateWorkflowVariableDeclaration,
} from "./workflowVariables";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";

function node(
  id: string,
  kind: WorkflowNode["data"]["kind"],
  data: Record<string, unknown> = {},
): WorkflowNode {
  return {
    id,
    type: "workflowNode",
    position: { x: 0, y: 0 },
    data: { kind, title: id, description: "", ...data },
  } as WorkflowNode;
}

function edge(
  source: string,
  target: string,
  extra: Partial<WorkflowEdge> = {},
): WorkflowEdge {
  return {
    id: `${source}-${target}-${extra.sourceHandle ?? "flow"}`,
    source,
    target,
    ...extra,
  } as WorkflowEdge;
}

function descriptor(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  selectedNodeId: string | null,
  name: string,
) {
  return analyzeWorkflowVariables(nodes, edges, selectedNodeId).find(
    (variable) => variable.name === name,
  );
}

describe("analyzeWorkflowVariables", () => {
  it("marks workflow inputs and file assets as globally available", () => {
    const nodes = [
      node("input", "input", { variableName: "request" }),
      node("document", "document_extractor", {
        assetIdVariable: "source_asset",
        outputVariable: "document_text",
      }),
      node("target", "llm"),
    ];

    expect(descriptor(nodes, [], "target", "request")?.availability).toBe(
      "available",
    );
    expect(
      descriptor(nodes, [], "target", "source_asset")?.valueType,
    ).toBe("file_asset");
    expect(
      descriptor(nodes, [], "target", "source_asset")?.availability,
    ).toBe("available");
  });

  it("registers deployment event, body, and resume outputs in the global inventory", () => {
    const nodes = [
      node("schedule", "scheduled_start", { eventVariable: "schedule_event" }),
      node("http", "http_event_entry", {
        eventVariable: "http_event",
        bodyVariable: "request_body",
      }),
      node("failure", "failure_event_entry", {
        eventVariable: "failure_event",
      }),
      node("call-entry", "workflow_call_entry", {
        eventVariable: "call_event",
      }),
      node("invoke", "invoke_workflow", {
        resultVariable: "workflow_result",
      }),
      node("wait", "suspend_wait", { outputVariable: "resume_event" }),
    ];

    const variables = analyzeWorkflowVariables(nodes, [], null);
    expect(variables.map((variable) => variable.name)).toEqual([
      "call_event",
      "failure_event",
      "http_event",
      "request_body",
      "resume_event",
      "schedule_event",
      "workflow_result",
    ]);
    expect(
      getWorkflowVariableFieldDescriptor("http_event_entry", "bodyVariable")?.mode,
    ).toBe("declaration");
    expect(
      getWorkflowVariableFieldDescriptor("failure_event_entry", "eventVariable")?.mode,
    ).toBe("declaration");
    expect(
      getWorkflowVariableFieldDescriptor("invoke_workflow", "resultVariable")?.mode,
    ).toBe("declaration");
  });

  it("distinguishes guaranteed, conditional, downstream, and unrelated outputs", () => {
    const nodes = [
      node("input", "input"),
      node("guaranteed", "code", { codeOutputVariable: "ready_value" }),
      node("branch", "llm", { outputVariable: "branch_value" }),
      node("other", "llm", { outputVariable: "unrelated_value" }),
      node("merge", "template_transform"),
      node("later", "json_serialize", { outputVariable: "later_value" }),
    ];
    const edges = [
      edge("input", "guaranteed"),
      edge("guaranteed", "branch"),
      edge("guaranteed", "merge"),
      edge("branch", "merge"),
      edge("merge", "later"),
    ];

    expect(
      descriptor(nodes, edges, "merge", "ready_value")?.availability,
    ).toBe("available");
    expect(
      descriptor(nodes, edges, "merge", "branch_value")?.availability,
    ).toBe("conditional");
    expect(
      descriptor(nodes, edges, "merge", "later_value")?.availability,
    ).toBe("unavailable");
    expect(
      descriptor(nodes, edges, "merge", "unrelated_value")?.availability,
    ).toBe("unavailable");
  });

  it("ignores resource bindings when calculating control flow", () => {
    const nodes = [
      node("resource", "code", { codeOutputVariable: "resource_value" }),
      node("target", "llm"),
    ];
    const edges = [
      edge("resource", "target", {
        sourceHandle: "knowledge-binding",
        targetHandle: "knowledge",
      }),
    ];

    const variable = descriptor(nodes, edges, "target", "resource_value");
    expect(variable?.availability).toBe("unavailable");
    expect(variable?.availabilityReason).toContain("旁支");
  });

  it("exposes duplicate producers as a conflict instead of silently deduplicating", () => {
    const nodes = [
      node("first", "llm", { outputVariable: "shared_output" }),
      node("second", "code", { codeOutputVariable: "shared_output" }),
      node("target", "template_transform"),
    ];
    const edges = [edge("first", "target"), edge("second", "target")];
    const variable = descriptor(nodes, edges, "target", "shared_output");

    expect(variable?.availability).toBe("conflict");
    expect(variable?.sources.map((source) => source.nodeId)).toEqual([
      "first",
      "second",
    ]);
  });

  it("protects cycles and current-node outputs from insertion", () => {
    const nodes = [
      node("first", "llm", { outputVariable: "first_output" }),
      node("second", "code", { codeOutputVariable: "second_output" }),
    ];
    const edges = [edge("first", "second"), edge("second", "first")];

    expect(
      descriptor(nodes, edges, "second", "first_output")?.availabilityReason,
    ).toContain("循环依赖");
    expect(
      descriptor(nodes, edges, "second", "second_output")?.availabilityReason,
    ).toContain("自己的输出");
  });

  it("collects structured and template references without leaking iteration locals", () => {
    const nodes = [
      node("producer", "llm", { outputVariable: "answer" }),
      node("iteration", "iteration", {
        inputVariable: "answer",
        iterationVariable: "item",
        itemTemplate: "{{item}} / {{answer}}",
        valueBindings: [{ source: "variable", variable: "answer" }],
      }),
    ];
    const variables = analyzeWorkflowVariables(
      nodes,
      [edge("producer", "iteration")],
      null,
    );

    expect(
      variables.find((variable) => variable.name === "answer")?.references,
    ).toHaveLength(3);
    expect(variables.some((variable) => variable.name === "item")).toBe(false);
  });

  it("removes deleted producers from the derived inventory", () => {
    const producer = node("producer", "llm", { outputVariable: "answer" });
    expect(analyzeWorkflowVariables([producer], [], null)).toHaveLength(1);
    expect(analyzeWorkflowVariables([], [], null)).toEqual([]);
  });

  it("keeps undefined legacy references visible without inventing a producer", () => {
    const target = node("target", "condition", {
      conditionVariable: "legacy_missing_value",
    });
    const variable = descriptor(
      [target],
      [],
      "target",
      "legacy_missing_value",
    );

    expect(variable?.sources).toEqual([]);
    expect(variable?.availability).toBe("unavailable");
    expect(variable?.availabilityReason).toContain("未找到变量生产者");
  });

  it("includes persisted workflow inputs and constants in the global inventory", () => {
    const declarations: WorkflowVariableDeclaration[] = [
      { id: "input-1", name: "locale", kind: "input", valueType: "text" },
      {
        id: "constant-1",
        name: "retry_limit",
        kind: "constant",
        valueType: "number",
        defaultValue: 3,
      },
    ];
    const variables = analyzeWorkflowVariables([], [], null, declarations);

    expect(variables.map((variable) => variable.name)).toEqual([
      "locale",
      "retry_limit",
    ]);
    expect(variables[1].sources[0]).toMatchObject({
      declarationId: "constant-1",
      sourceKind: "workflow_constant",
      valueType: "number",
    });
  });

  it("validates declaration names, values, secrets, paths, and node conflicts", () => {
    const existing: WorkflowVariableDeclaration[] = [
      { id: "existing", name: "locale", kind: "input", valueType: "text" },
    ];
    const nodes = [
      node("entry", "input", { variableName: "user_input" }),
      node("producer", "llm", { outputVariable: "answer" }),
    ];

    expect(
      validateWorkflowVariableDeclaration(
        {
          id: "bad",
          name: "api_key",
          kind: "constant",
          valueType: "json",
          defaultValue: { path: "C:\\private\\secret.txt" },
        },
        existing,
        nodes,
      ),
    ).toEqual(
      expect.arrayContaining([
        expect.stringContaining("密钥"),
        expect.stringContaining("绝对路径"),
      ]),
    );
    expect(
      validateWorkflowVariableDeclaration(
        { id: "collision", name: "answer", kind: "input", valueType: "text" },
        existing,
        nodes,
      ),
    ).toContain("名称与节点输出变量冲突。");
    expect(
      validateWorkflowVariableDeclaration(
        { id: "input-collision", name: "user_input", kind: "input", valueType: "text" },
        existing,
        nodes,
      ),
    ).toContain("名称与节点输出变量冲突。");
    expect(
      validateWorkflowVariableDeclaration(
        {
          id: "unsafe-value",
          name: "service_value",
          kind: "constant",
          valueType: "text",
          defaultValue: "sk-abcdefghijklmnop",
        },
        existing,
        nodes,
      ),
    ).toContain("变量值不能包含绝对路径或明显凭据字段。");
  });

  it("previews and atomically rewrites exact declaration, binding, template, and structured references", () => {
    const declarations: WorkflowVariableDeclaration[] = [
      { id: "input-1", name: "request", kind: "input", valueType: "text" },
    ];
    const nodes = [
      node("condition", "condition", { conditionVariable: "request" }),
      node("template", "template_transform", {
        template: "收到 {{ request }}",
        valueBindings: { body: { source: "variable", variable: "request" } },
      }),
      node("invoke", "invoke_workflow", {
        inputBindings: {
          message: { source: "variable", variable: "request" },
        },
        resultVariable: "workflow_result",
      }),
    ];
    const plan = planWorkflowVariableRename(
      "request",
      "customer_request",
      nodes,
      [],
      declarations,
    );

    expect(plan.allowed).toBe(true);
    expect(plan.declarations[0].name).toBe("customer_request");
    expect(plan.nodes[0].data.conditionVariable).toBe("customer_request");
    expect(plan.nodes[1].data.template).toBe("收到 {{ customer_request }}");
    expect(plan.nodes[1].data.valueBindings).toEqual({
      body: { source: "variable", variable: "customer_request" },
    });
    expect(plan.nodes[2].data.inputBindings).toEqual({
      message: { source: "variable", variable: "customer_request" },
    });
  });

  it("blocks automatic rename when free text may refer to the variable", () => {
    const declarations: WorkflowVariableDeclaration[] = [
      { id: "input-1", name: "request", kind: "input", valueType: "text" },
    ];
    const nodes = [
      node("custom", "annotation", { note: "read request before continuing" }),
    ];
    const plan = planWorkflowVariableRename(
      "request",
      "customer_request",
      nodes,
      [],
      declarations,
    );

    expect(plan.allowed).toBe(false);
    expect(plan.blockers.join(" ")).toContain("自由文本引用");
  });

  it("describes declarations and iteration locals without promoting them to upstream bindings", () => {
    expect(
      getWorkflowVariableFieldDescriptor("document_extractor", "assetIdVariable")
        ?.mode,
    ).toBe("declaration");
    expect(
      getWorkflowVariableFieldDescriptor("iteration", "itemTemplate")
        ?.localVariables,
    ).toEqual([
      { name: "item", label: "当前迭代项", valueType: "unknown" },
    ]);
  });

  it("prefers complete NodeContract input types and falls back for compatibility contracts", () => {
    const descriptor = getWorkflowVariableFieldDescriptor(
      "parameter_extractor",
      "inputVariable",
    );
    expect(descriptor).not.toBeNull();
    const completeContract = {
      kind: "parameter_extractor",
      contract_status: "complete",
      ports: [
        {
          name: "text",
          direction: "input",
          value_schema: { type: "object" },
          required: true,
          cardinality: "one",
          binding: "variable",
        },
      ],
    } as WorkflowNodeContractProjection;
    const compatibilityContract = {
      ...completeContract,
      contract_status: "compatibility",
    } as WorkflowNodeContractProjection;

    expect(resolveWorkflowVariableFieldTypes(descriptor!, completeContract)).toEqual([
      "json",
    ]);
    expect(
      resolveWorkflowVariableFieldTypes(descriptor!, compatibilityContract),
    ).toEqual(["text", "unknown"]);
  });

  it.each([
    ["condition", "conditionVariable", "binding"],
    ["condition", "inputVariable", "binding"],
    ["code", "codeInputVariable", "binding"],
    ["variable_assign", "template", "template"],
    ["knowledge_retrieval", "queryVariable", "binding"],
    ["http_request", "url", "template"],
    ["http_request", "bodyVariable", "binding"],
    ["http_request", "outputVariable", "declaration"],
    ["list_operation", "inputVariable", "binding"],
    ["dataset_compare", "leftVariable", "binding"],
    ["dataset_compare", "rightVariable", "binding"],
    ["dataset_compare", "outputVariable", "declaration"],
    ["iteration", "itemTemplate", "template"],
    ["document_extractor", "assetIdVariable", "declaration"],
    ["workflow_agent", "taskInput", "template"],
    ["agent_handoff", "taskIdVariable", "binding"],
    ["output", "outputVariable", "binding"],
    ["data_table_query", "outputVariable", "declaration"],
  ] as const)(
    "registers %s.%s as a %s field",
    (kind, fieldName, mode) => {
      expect(getWorkflowVariableFieldDescriptor(kind, fieldName)?.mode).toBe(mode);
    },
  );
});
