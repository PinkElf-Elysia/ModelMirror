import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WorkflowEdge, WorkflowNode } from "../../types/workflow";
import WorkflowVariableField from "./WorkflowVariableField";
import { getWorkflowVariableFieldDescriptor } from "./workflowVariables";

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

function edge(source: string, target: string): WorkflowEdge {
  return { id: `${source}-${target}`, source, target } as WorkflowEdge;
}

describe("WorkflowVariableField", () => {
  it("replaces a raw binding while preserving manual unknown values", () => {
    const producer = node("input", "input", { variableName: "request" });
    const target = node("condition", "condition", {
      conditionVariable: "legacy_value",
    });
    const onChange = vi.fn();
    render(
      <WorkflowVariableField
        edges={[edge("input", "condition")]}
        fieldName="conditionVariable"
        node={target}
        nodes={[producer, target]}
        onChange={onChange}
        value="legacy_value"
      />,
    );

    expect(screen.getByDisplayValue("legacy_value")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("保留旧引用但不会自动改写");
    fireEvent.click(screen.getByRole("button", { name: /绑定变量/ }));
    fireEvent.click(screen.getByRole("button", { name: /request/ }));
    expect(onChange).toHaveBeenCalledWith("request");
  });

  it("appends unique values for a multi-variable binding", () => {
    const first = node("first", "input", { variableName: "first_value" });
    const second = node("second", "input", { variableName: "second_value" });
    const target = node("aggregate", "variable_aggregator");
    const onChange = vi.fn();
    render(
      <WorkflowVariableField
        edges={[]}
        fieldName="variableNames"
        node={target}
        nodes={[first, second, target]}
        onChange={onChange}
        value="first_value"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /绑定变量/ }));
    fireEvent.click(screen.getByRole("button", { name: /second_value/ }));
    expect(onChange).toHaveBeenCalledWith("first_value, second_value");
  });

  it("inserts a template token at the current selection and restores focus", async () => {
    const producer = node("input", "input", { variableName: "request" });
    const target = node("llm", "llm");
    let value = "Hello world";
    const onChange = vi.fn((next: string) => {
      value = next;
    });
    const { rerender } = render(
      <WorkflowVariableField
        edges={[edge("input", "llm")]}
        fieldName="prompt"
        multiline
        node={target}
        nodes={[producer, target]}
        onChange={onChange}
        value={value}
      />,
    );
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    textarea.focus();
    textarea.setSelectionRange(6, 11);
    fireEvent.click(screen.getByRole("button", { name: /插入变量/ }));
    fireEvent.click(screen.getByRole("button", { name: /request/ }));
    expect(onChange).toHaveBeenCalledWith("Hello {{request}}");

    rerender(
      <WorkflowVariableField
        edges={[edge("input", "llm")]}
        fieldName="prompt"
        multiline
        node={target}
        nodes={[producer, target]}
        onChange={onChange}
        value={value}
      />,
    );
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveFocus());
  });

  it("preserves and warns about unknown references in a legacy template", () => {
    const target = node("llm", "llm");
    render(
      <WorkflowVariableField
        edges={[]}
        fieldName="prompt"
        multiline
        node={target}
        nodes={[target]}
        onChange={vi.fn()}
        value="Keep {{legacy_result}} unchanged"
      />,
    );

    expect(screen.getByDisplayValue("Keep {{legacy_result}} unchanged")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "legacy_result：未找到变量生产者，旧引用会原样保留",
    );
  });

  it("searches disabled variables, explains the reason, and closes with Escape", async () => {
    const branch = node("branch", "llm", { outputVariable: "branch_value" });
    const start = node("start", "input", { variableName: "request" });
    const target = node("selected", "template_transform");
    const descriptor = getWorkflowVariableFieldDescriptor(
      "template_transform",
      "template",
    )!;
    render(
      <WorkflowVariableField
        descriptor={descriptor}
        edges={[edge("start", "branch"), edge("start", "selected"), edge("branch", "selected")]}
        fieldName="template"
        node={target}
        nodes={[start, branch, target]}
        onChange={vi.fn()}
        value=""
      />,
    );

    const trigger = screen.getByRole("button", { name: /插入变量/ });
    fireEvent.click(trigger);
    fireEvent.change(screen.getByPlaceholderText("搜索名称、来源或状态"), {
      target: { value: "branch_value" },
    });
    const option = screen.getByRole("button", { name: /branch_value/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    expect(option).toHaveAttribute("title", expect.stringContaining("非必经分支"));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("accepts a variable produced on the matching data merge branch", () => {
    const start = node("start", "input", { variableName: "request" });
    const left = node("left", "list_operation", { outputVariable: "left_rows" });
    const right = node("right", "list_operation", { outputVariable: "right_rows" });
    const merge = node("merge", "data_merge", { leftVariable: "left_rows" });
    const edges: WorkflowEdge[] = [
      edge("start", "left"),
      edge("start", "right"),
      { ...edge("left", "merge"), id: "left-merge", targetHandle: "left" },
      { ...edge("right", "merge"), id: "right-merge", targetHandle: "right" },
    ];

    render(
      <WorkflowVariableField
        edges={edges}
        fieldName="leftVariable"
        node={merge}
        nodes={[start, left, right, merge]}
        onChange={vi.fn()}
        value="left_rows"
      />,
    );

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /绑定变量/ }));
    expect(screen.getByRole("button", { name: /left_rows/ })).not.toHaveAttribute(
      "aria-disabled",
    );
    expect(screen.getByRole("button", { name: /right_rows/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });
});
