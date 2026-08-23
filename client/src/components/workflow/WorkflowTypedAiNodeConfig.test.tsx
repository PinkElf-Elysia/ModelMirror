import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WorkflowEdge, WorkflowNode } from "../../types/workflow";
import { createNodeData } from "./WorkflowEditor";
import {
  ParameterExtractorConfig,
  QuestionClassifierConfig,
} from "./WorkflowTypedAiNodeConfig";

const common = (node: WorkflowNode, edges: WorkflowEdge[], onChange = vi.fn()) => ({
  contract: null,
  data: node.data,
  declarations: [],
  edges,
  models: [{ id: "test/model", name: "Test Model" }],
  node,
  nodes: [node],
  onChange,
  onMigrate: () => "",
  onOpenVariableCenter: vi.fn(),
});

describe("WorkflowTypedAiNodeConfig", () => {
  it("keeps classifier IDs stable while reordering and blocks connected deletion", () => {
    const node = {
      id: "classifier",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: createNodeData("question_classifier"),
    } as WorkflowNode;
    const edge = {
      id: "connected",
      source: node.id,
      sourceHandle: "category_1",
      target: "target",
    } as WorkflowEdge;
    const onChange = vi.fn();
    render(<QuestionClassifierConfig {...common(node, [edge], onChange)} />);

    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[0]);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/category_1 仍有连线/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "上移" })[1]);
    expect(onChange).toHaveBeenLastCalledWith({
      categoriesV2: [
        expect.objectContaining({ id: "category_2" }),
        expect.objectContaining({ id: "category_1" }),
      ],
    });
  });

  it("offers structured fields and an explicit one-call repair choice", () => {
    const node = {
      id: "extractor",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: createNodeData("parameter_extractor"),
    } as WorkflowNode;
    const onChange = vi.fn();
    render(<ParameterExtractorConfig {...common(node, [], onChange)} />);

    expect(screen.getByRole("textbox", { name: "field_1 字段名" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "field_1 字段类型" })).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("不追加调用（默认）"), {
      target: { value: "1" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ repairAttempts: 1 });
  });

  it("starts advanced list schemas with an object-array root", () => {
    const node = {
      id: "extractor-list",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        ...createNodeData("parameter_extractor"),
        outputShape: "object_list",
        jsonSchema: {},
      },
    } as WorkflowNode;
    const onChange = vi.fn();
    render(<ParameterExtractorConfig {...common(node, [], onChange)} />);

    fireEvent.change(screen.getByDisplayValue("字段表"), {
      target: { value: "json_schema" },
    });

    expect(onChange).toHaveBeenLastCalledWith({
      schemaMode: "json_schema",
      jsonSchema: {
        type: "array",
        items: { type: "object", properties: {}, additionalProperties: false },
      },
    });
  });

  it("keeps a starter advanced schema aligned when output shape changes", () => {
    const node = {
      id: "extractor-shape",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        ...createNodeData("parameter_extractor"),
        schemaMode: "json_schema",
        outputShape: "object",
        jsonSchema: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
      },
    } as WorkflowNode;
    const onChange = vi.fn();
    render(<ParameterExtractorConfig {...common(node, [], onChange)} />);

    fireEvent.change(screen.getByDisplayValue("单个对象"), {
      target: { value: "object_list" },
    });

    expect(onChange).toHaveBeenLastCalledWith({
      outputShape: "object_list",
      jsonSchema: {
        type: "array",
        items: { type: "object", properties: {}, additionalProperties: false },
      },
    });
  });

  it("does not overwrite a customized advanced schema when output shape changes", () => {
    const jsonSchema = {
      type: "object",
      properties: { order_id: { type: "string" } },
      required: ["order_id"],
      additionalProperties: false,
    };
    const node = {
      id: "extractor-custom-schema",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        ...createNodeData("parameter_extractor"),
        schemaMode: "json_schema",
        outputShape: "object",
        jsonSchema,
      },
    } as WorkflowNode;
    const onChange = vi.fn();
    render(<ParameterExtractorConfig {...common(node, [], onChange)} />);

    fireEvent.change(screen.getByDisplayValue("单个对象"), {
      target: { value: "object_list" },
    });

    expect(onChange).toHaveBeenLastCalledWith({ outputShape: "object_list" });
  });

  it("keeps all legacy classifier options editable before explicit migration", () => {
    const node = {
      id: "legacy-classifier",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        kind: "question_classifier",
        title: "旧问题分类器",
        description: "legacy",
        inputVariable: "user_input",
        outputVariable: "category",
        categories: '{"退款":["退款"],"物流":["物流"]}',
        defaultCategory: "其他",
        matchMode: "contains_any",
        caseSensitive: "false",
        useLlmFallback: "true",
        modelId: "test/model",
        llmFallbackPrompt: "只返回类别名称",
      },
    } as WorkflowNode;

    render(<QuestionClassifierConfig {...common(node, [])} />);

    expect(screen.getByDisplayValue("其他")).toBeInTheDocument();
    expect(screen.getByDisplayValue("任一关键词")).toBeInTheDocument();
    expect(screen.getByText("回退模型")).toBeInTheDocument();
    expect(screen.getByDisplayValue("只返回类别名称")).toBeInTheDocument();
  });
});
