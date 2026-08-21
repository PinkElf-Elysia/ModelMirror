import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import { createNodeData } from "./WorkflowEditor";
import WorkflowControlDataNodeConfig, {
  comparisonValueText,
} from "./WorkflowControlDataNodeConfig";


function node(kind: Parameters<typeof createNodeData>[0]): WorkflowNode {
  return {
    id: `${kind}-1`,
    type: "workflowNode",
    position: { x: 0, y: 0 },
    data: createNodeData(kind),
  };
}

describe("WorkflowControlDataNodeConfig", () => {
  it("keeps route ids stable while reordering and blocks connected deletion", () => {
    const routeNode = node("multi_route");
    const target = node("output");
    const edges: WorkflowEdge[] = [
      {
        id: "connected-route",
        source: routeNode.id,
        sourceHandle: "route_1",
        target: target.id,
      },
    ];
    const onChange = vi.fn();
    const onOpenVariableCenter = vi.fn();

    render(
      <WorkflowControlDataNodeConfig
        data={routeNode.data}
        edges={edges}
        node={routeNode}
        nodes={[routeNode, target]}
        onChange={onChange}
        onOpenVariableCenter={onOpenVariableCenter}
      />,
    );

    expect(screen.getAllByRole("button", { name: "删除规则" })[0]).toBeDisabled();
    expect(screen.getByText("已连线，需先删除连线")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "上移" })[1]);
    expect(onChange).toHaveBeenLastCalledWith({
      routes: [
        expect.objectContaining({ id: "route_2" }),
        expect.objectContaining({ id: "route_1" }),
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "管理全局变量" }));
    expect(onOpenVariableCenter).toHaveBeenCalledOnce();
  });

  it("renders structured filter controls without requiring handwritten rule JSON", () => {
    const listNode = node("list_operation");
    listNode.data.operator = "filter";
    const onChange = vi.fn();
    render(
      <WorkflowControlDataNodeConfig
        data={listNode.data}
        edges={[]}
        node={listNode}
        nodes={[listNode]}
        onChange={onChange}
        onOpenVariableCenter={vi.fn()}
      />,
    );

    expect(screen.getByRole("combobox", { name: "筛选规则 1 比较运算符" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "筛选规则 1 顶层字段" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "筛选规则 1 比较文本" })).toBeInTheDocument();
    expect(screen.queryByText(/规则 JSON/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "筛选规则 1 比较运算符" }), {
      target: { value: "gt" },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      filterRules: [
        {
          field: "",
          operator: "gt",
          valueType: "number",
          value: 0,
        },
      ],
    });
  });

  it("accepts workflow-level variables in control data bindings", () => {
    const listNode = node("list_operation");
    listNode.data.inputVariable = "items";
    const declarations: WorkflowVariableDeclaration[] = [
      {
        id: "input-items",
        name: "items",
        kind: "input",
        valueType: "json",
      },
    ];

    render(
      <WorkflowControlDataNodeConfig
        data={listNode.data}
        declarations={declarations}
        edges={[]}
        node={listNode}
        nodes={[listNode]}
        onChange={vi.fn()}
        onOpenVariableCenter={vi.fn()}
      />,
    );

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /绑定变量/ }));
    expect(screen.getByText("items", { selector: "span" })).toBeInTheDocument();
  });

  it("adds aggregate measures with explicit field and operation controls", () => {
    const aggregateNode = node("data_aggregate");
    const onChange = vi.fn();
    render(
      <WorkflowControlDataNodeConfig
        data={aggregateNode.data}
        edges={[]}
        node={aggregateNode}
        nodes={[aggregateNode]}
        onChange={onChange}
        onOpenVariableCenter={vi.fn()}
      />,
    );

    expect(screen.getByRole("textbox", { name: "度量 1 输出字段" })).toHaveValue("row_count");
    expect(screen.getByRole("combobox", { name: "度量 1 操作" })).toHaveValue("count");
    fireEvent.click(screen.getByRole("button", { name: "添加度量" }));
    expect(onChange).toHaveBeenLastCalledWith({
      measures: [
        { outputField: "row_count", operation: "count" },
        { outputField: "measure_1", operation: "count" },
      ],
    });
  });

  it("reuses the first free aggregate output name after a deletion", () => {
    const aggregateNode = node("data_aggregate");
    aggregateNode.data.measures = [
      { outputField: "measure_2", operation: "count" },
    ];
    const onChange = vi.fn();
    render(
      <WorkflowControlDataNodeConfig
        data={aggregateNode.data}
        edges={[]}
        node={aggregateNode}
        nodes={[aggregateNode]}
        onChange={onChange}
        onOpenVariableCenter={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "添加度量" }));
    expect(onChange).toHaveBeenLastCalledWith({
      measures: [
        { outputField: "measure_2", operation: "count" },
        { outputField: "measure_1", operation: "count" },
      ],
    });
  });

  it("gives repeated route controls stable row-specific accessible names", () => {
    const routeNode = node("multi_route");

    render(
      <WorkflowControlDataNodeConfig
        data={routeNode.data}
        edges={[]}
        node={routeNode}
        nodes={[routeNode]}
        onChange={vi.fn()}
        onOpenVariableCenter={vi.fn()}
      />,
    );

    expect(screen.getByRole("combobox", { name: "route_1 比较运算符" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "route_2 比较运算符" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "比较运算符" })).not.toBeInTheDocument();
  });

  it("formats typed comparison values deterministically", () => {
    expect(comparisonValueText({ z: 1, a: [true, null] })).toBe(
      '{\n  "z": 1,\n  "a": [\n    true,\n    null\n  ]\n}',
    );
    expect(comparisonValueText("plain")).toBe("plain");
  });
});
