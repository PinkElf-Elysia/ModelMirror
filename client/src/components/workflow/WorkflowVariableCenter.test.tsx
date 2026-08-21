import { fireEvent, render, screen, within } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import type { WorkflowNode, WorkflowVariableDeclaration } from "../../types/workflow";
import WorkflowVariableCenter from "./WorkflowVariableCenter";
import type {
  WorkflowVariableDescriptor,
  WorkflowVariableRenamePlan,
} from "./workflowVariables";

const variables: WorkflowVariableDescriptor[] = [
  {
    name: "user_input",
    valueType: "text",
    availability: "available",
    availabilityReason: "运行开始时由工作流输入提供。",
    references: [],
    sources: [
      {
        nodeId: "input",
        nodeTitle: "开始",
        nodeKind: "input",
        field: "variableName",
        sourceKind: "workflow_input",
        valueType: "text",
        conditional: false,
      },
    ],
  },
  {
    name: "branch_value",
    valueType: "json",
    availability: "conditional",
    availabilityReason: "变量来自非必经分支，运行时可能不存在。",
    references: [
      {
        nodeId: "selected",
        nodeTitle: "当前节点",
        nodeKind: "llm",
        field: "prompt",
        mode: "template",
        expectedTypes: ["text", "json", "unknown"],
        editable: true,
      },
    ],
    sources: [
      {
        nodeId: "branch",
        nodeTitle: "分支检索",
        nodeKind: "knowledge_retrieval",
        field: "outputVariable",
        sourceKind: "node_output",
        valueType: "json",
        conditional: false,
      },
    ],
  },
];

const selectedNode = {
  id: "selected",
  type: "workflowNode",
  position: { x: 0, y: 0 },
  data: { kind: "llm", title: "当前节点", description: "" },
} as WorkflowNode;

function renderCenter(overrides: {
  onClose?: () => void;
  onCreate?: (declaration: WorkflowVariableDeclaration) => void;
  onDelete?: (declarationId: string) => string | null;
  onLocateSource?: (nodeId: string) => void;
  onPlanRename?: (oldName: string, newName: string) => WorkflowVariableRenamePlan;
  declarations?: WorkflowVariableDeclaration[];
  nodes?: WorkflowNode[];
  variables?: WorkflowVariableDescriptor[];
  selectedNode?: WorkflowNode | null;
} = {}) {
  const triggerRef = createRef<HTMLButtonElement>();
  const onClose = overrides.onClose ?? vi.fn();
  const onLocateSource = overrides.onLocateSource ?? vi.fn();
  render(
    <>
      <button ref={triggerRef} type="button">
        打开变量
      </button>
      <WorkflowVariableCenter
        declarations={overrides.declarations ?? []}
        nodes={overrides.nodes ?? [selectedNode]}
        onApplyRename={vi.fn()}
        onClose={onClose}
        onCreate={overrides.onCreate ?? vi.fn()}
        onDelete={overrides.onDelete ?? vi.fn(() => null)}
        onLocateSource={onLocateSource}
        onPlanRename={overrides.onPlanRename ?? vi.fn(() => ({
          allowed: false,
          oldName: "",
          newName: "",
          changes: [],
          blockers: [],
          nodes: [],
          declarations: [],
        }))}
        onUpdate={vi.fn()}
        open
        selectedNode={
          Object.prototype.hasOwnProperty.call(overrides, "selectedNode")
            ? (overrides.selectedNode ?? null)
            : selectedNode
        }
        triggerRef={triggerRef}
        variables={overrides.variables ?? variables}
      />
    </>,
  );
  return { onClose, onLocateSource, triggerRef };
}

describe("WorkflowVariableCenter", () => {
  it("shows status, source, references, and filters by source node", () => {
    renderCenter();
    const dialog = screen.getByRole("dialog", { name: "工作流变量" });

    expect(within(dialog).getByText("条件可用")).toBeInTheDocument();
    expect(within(dialog).getByText("结构化数据 · 1 处引用")).toBeInTheDocument();

    fireEvent.change(within(dialog).getByRole("textbox"), {
      target: { value: "分支检索" },
    });
    expect(within(dialog).getByText("branch_value")).toBeInTheDocument();
    expect(within(dialog).queryByText("user_input")).not.toBeInTheDocument();
  });

  it("locates a source node and closes the drawer", () => {
    const onClose = vi.fn();
    const onLocateSource = vi.fn();
    renderCenter({ onClose, onLocateSource });

    fireEvent.click(screen.getByRole("button", { name: /branch_value/ }));
    fireEvent.click(screen.getByRole("button", { name: /分支检索/ }));
    expect(onLocateSource).toHaveBeenCalledWith("branch");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes with Escape and restores focus to the toolbar trigger", () => {
    const { onClose, triggerRef } = renderCenter();
    triggerRef.current?.focus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    return new Promise<void>((resolve) => {
      window.requestAnimationFrame(() => {
        expect(document.activeElement).toBe(triggerRef.current);
        resolve();
      });
    });
  });

  it("stays in inventory mode when no node is selected", () => {
    renderCenter({ selectedNode: null });
    expect(
      screen.getByText("输入与常量属于当前工作流；节点输出保持只读。"),
    ).toBeInTheDocument();
  });

  it("creates a typed workflow input from the variable center", () => {
    const onCreate = vi.fn();
    renderCenter({ onCreate });

    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    const dialog = screen.getByRole("dialog", { name: "新建变量" });
    fireEvent.change(within(dialog).getByLabelText("名称"), {
      target: { value: "locale" },
    });
    fireEvent.change(within(dialog).getByLabelText("默认值（可选）"), {
      target: { value: "zh-CN" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存变量" }));

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "locale",
        kind: "input",
        valueType: "text",
        defaultValue: "zh-CN",
      }),
    );
  });

  it("blocks a declaration that collides with the manual entry variable", () => {
    const onCreate = vi.fn();
    const inputNode = {
      id: "input",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        kind: "input",
        title: "开始",
        description: "",
        variableName: "user_input",
      },
    } as WorkflowNode;
    renderCenter({ nodes: [inputNode, selectedNode], onCreate });

    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    const dialog = screen.getByRole("dialog", { name: "新建变量" });
    fireEvent.change(within(dialog).getByLabelText("名称"), {
      target: { value: "user_input" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存变量" }));

    expect(within(dialog).getByText("名称与节点输出变量冲突。")).toBeInTheDocument();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("shows exact rename changes and keeps referenced declarations protected", () => {
    const declaration: WorkflowVariableDeclaration = {
      id: "input-request",
      name: "request",
      kind: "input",
      valueType: "text",
    };
    const declarationVariable: WorkflowVariableDescriptor = {
      name: "request",
      valueType: "text",
      availability: "available",
      availabilityReason: "运行开始时由工作流输入提供。",
      sources: [
        {
          nodeId: "workflow-variable:input-request",
          nodeTitle: "工作流输入",
          nodeKind: "input",
          field: "name",
          sourceKind: "workflow_input",
          valueType: "text",
          conditional: false,
          declarationId: "input-request",
        },
      ],
      references: [
        {
          nodeId: "selected",
          nodeTitle: "当前节点",
          nodeKind: "llm",
          field: "prompt",
          mode: "template",
          expectedTypes: ["text", "json", "unknown"],
          editable: true,
        },
      ],
    };
    const onPlanRename = vi.fn(() => ({
      allowed: true,
      oldName: "request",
      newName: "customer_request",
      blockers: [],
      changes: [
        {
          nodeId: "selected",
          nodeTitle: "当前节点",
          field: "prompt",
          mode: "template" as const,
        },
      ],
      nodes: [],
      declarations: [],
    }));

    renderCenter({
      declarations: [declaration],
      variables: [declarationVariable],
      onPlanRename,
    });
    fireEvent.click(screen.getByRole("button", { name: /request/ }));
    expect(screen.getByRole("button", { name: "删除" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "改名" }));
    fireEvent.change(screen.getByLabelText("新名称"), {
      target: { value: "customer_request" },
    });
    expect(onPlanRename).toHaveBeenCalledWith("request", "customer_request");
    expect(screen.getByText(/当前节点 · prompt/)).toBeInTheDocument();
  });
});
