import { describe, expect, it } from "vitest";

import type { WorkflowEdge, WorkflowNode } from "../../types/workflow";
import {
  collectUpstreamInsertableVariables,
  collectWorkflowVariableOptions,
} from "./VariableInsertMenu";
import { getWorkflowVariableFieldDescriptor } from "./workflowVariables";

function workflowNode(
  id: string,
  data: Record<string, unknown>,
): WorkflowNode {
  return {
    id,
    type: String(data.kind ?? "input"),
    position: { x: 0, y: 0 },
    data: {
      title: id,
      description: "",
      ...data,
    },
  } as WorkflowNode;
}

function workflowEdge(
  id: string,
  source: string,
  target: string,
  extra: Partial<WorkflowEdge> = {},
): WorkflowEdge {
  return { id, source, target, ...extra } as WorkflowEdge;
}

describe("collectUpstreamInsertableVariables", () => {
  it("only exposes control-flow ancestors of the selected node", () => {
    const nodes = [
      workflowNode("input", {
        kind: "input",
        variableName: "user_input",
      }),
      workflowNode("prepare", {
        kind: "variable_assign",
        variableName: "prepared_text",
      }),
      workflowNode("selected", {
        kind: "llm",
        outputVariable: "selected_output",
      }),
      workflowNode("unrelated", {
        kind: "code",
        codeOutputVariable: "secret_unrelated_output",
      }),
    ];
    const edges = [
      workflowEdge("input-prepare", "input", "prepare"),
      workflowEdge("prepare-selected", "prepare", "selected"),
      workflowEdge("resource-binding", "unrelated", "selected", {
        sourceHandle: "knowledge-binding",
        targetHandle: "knowledge",
      }),
    ];

    expect(
      collectUpstreamInsertableVariables("selected", nodes, edges),
    ).toEqual([
      { name: "user_input", label: "input" },
      { name: "prepared_text", label: "prepare" },
    ]);
  });

  it("shows branch, downstream, conflict, and type-mismatch variables but disables them", () => {
    const nodes = [
      workflowNode("start", { kind: "input", variableName: "request" }),
      workflowNode("json", {
        kind: "knowledge_retrieval",
        outputVariable: "records",
      }),
      workflowNode("branch", { kind: "llm", outputVariable: "branch_text" }),
      workflowNode("duplicate-a", { kind: "llm", outputVariable: "duplicate" }),
      workflowNode("duplicate-b", { kind: "code", codeOutputVariable: "duplicate" }),
      workflowNode("selected", { kind: "parameter_extractor" }),
      workflowNode("later", { kind: "llm", outputVariable: "later_text" }),
    ];
    const edges = [
      workflowEdge("start-json", "start", "json"),
      workflowEdge("json-selected", "json", "selected"),
      workflowEdge("start-selected", "start", "selected"),
      workflowEdge("start-branch", "start", "branch"),
      workflowEdge("branch-selected", "branch", "selected"),
      workflowEdge("duplicate-a-selected", "duplicate-a", "selected"),
      workflowEdge("duplicate-b-selected", "duplicate-b", "selected"),
      workflowEdge("selected-later", "selected", "later"),
    ];
    const descriptor = getWorkflowVariableFieldDescriptor(
      "parameter_extractor",
      "inputVariable",
    )!;
    const options = collectWorkflowVariableOptions(
      "selected",
      nodes,
      edges,
      descriptor,
    );

    expect(options.find((item) => item.name === "request")?.disabled).toBe(false);
    expect(options.find((item) => item.name === "records")?.disabledReason).toContain(
      "类型不匹配",
    );
    expect(options.find((item) => item.name === "branch_text")?.disabled).toBe(
      true,
    );
    expect(options.find((item) => item.name === "duplicate")?.availability).toBe(
      "conflict",
    );
    expect(options.find((item) => item.name === "later_text")?.disabledReason).toContain(
      "下游",
    );
  });

  it("adds the iteration local only to the item template selector", () => {
    const nodes = [
      workflowNode("start", { kind: "input", variableName: "items" }),
      workflowNode("iteration", {
        kind: "iteration",
        iterationVariable: "row",
      }),
    ];
    const descriptor = getWorkflowVariableFieldDescriptor(
      "iteration",
      "itemTemplate",
    )!;
    const options = collectWorkflowVariableOptions(
      "iteration",
      nodes,
      [workflowEdge("start-iteration", "start", "iteration")],
      descriptor,
    );

    expect(options[0]).toMatchObject({
      name: "row",
      label: "当前批次项",
      local: true,
      disabled: false,
    });

    const v2Nodes = [
      nodes[0],
      workflowNode("iteration", {
        kind: "iteration",
        contractVersion: 2,
        itemVariable: "row",
        indexVariable: "row_index",
      }),
    ];
    const v2Options = collectWorkflowVariableOptions(
      "iteration",
      v2Nodes,
      [workflowEdge("start-iteration", "start", "iteration")],
      descriptor,
    );
    expect(v2Options.slice(0, 2)).toMatchObject([
      { name: "row", label: "当前批次项", local: true, disabled: false },
      { name: "row_index", label: "当前序号", local: true, disabled: false },
    ]);
  });

  it("scopes data merge variables to the matching input handle", () => {
    const nodes = [
      workflowNode("start", { kind: "input", variableName: "request" }),
      workflowNode("left-source", {
        kind: "list_operation",
        outputVariable: "left_rows",
      }),
      workflowNode("right-source", {
        kind: "list_operation",
        outputVariable: "right_rows",
      }),
      workflowNode("merge", { kind: "data_merge" }),
    ];
    const edges = [
      workflowEdge("start-left", "start", "left-source"),
      workflowEdge("start-right", "start", "right-source"),
      workflowEdge("left-merge", "left-source", "merge", { targetHandle: "left" }),
      workflowEdge("right-merge", "right-source", "merge", { targetHandle: "right" }),
    ];
    const descriptor = getWorkflowVariableFieldDescriptor(
      "data_merge",
      "leftVariable",
    )!;

    const options = collectWorkflowVariableOptions(
      "merge",
      nodes,
      edges,
      descriptor,
    );

    expect(options.find((item) => item.name === "left_rows")).toMatchObject({
      availability: "available",
      disabled: false,
    });
    expect(options.find((item) => item.name === "right_rows")).toMatchObject({
      availability: "unavailable",
      disabled: true,
    });
    expect(
      options.find((item) => item.name === "right_rows")?.disabledReason,
    ).toContain("左侧数据");
  });
});
