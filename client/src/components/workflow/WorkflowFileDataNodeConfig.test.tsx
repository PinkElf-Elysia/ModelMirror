import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WorkflowNode, WorkflowVariableDeclaration } from "../../types/workflow";
import { createNodeData } from "./WorkflowEditor";
import WorkflowFileDataNodeConfig from "./WorkflowFileDataNodeConfig";


function node(kind: "time_tool" | "file_output"): WorkflowNode {
  return {
    id: `${kind}-1`,
    type: "workflowNode",
    position: { x: 0, y: 0 },
    data: createNodeData(kind),
  };
}

const declarations: WorkflowVariableDeclaration[] = [
  { id: "input-source", name: "source_time", kind: "input", valueType: "text" },
  { id: "input-compare", name: "compare_time", kind: "input", valueType: "text" },
  { id: "input-report", name: "report_content", kind: "input", valueType: "json" },
];

describe("WorkflowFileDataNodeConfig", () => {
  it("presents timezone time operations as guided fields", () => {
    const timeNode = node("time_tool");
    timeNode.data.operation = "difference";
    timeNode.data.unit = "hours";
    const onChange = vi.fn();
    render(
      <WorkflowFileDataNodeConfig
        data={timeNode.data}
        declarations={declarations}
        edges={[]}
        node={timeNode}
        nodes={[timeNode]}
        onChange={onChange}
        onOpenVariableCenter={vi.fn()}
      />,
    );

    expect(screen.getByRole("combobox", { name: "要做什么" })).toHaveValue("difference");
    expect(screen.getByRole("combobox", { name: /时区/ })).toHaveValue("UTC");
    expect(screen.getByText("结果 = 来源时间 − 对照时间。")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "结果单位" }), {
      target: { value: "days" },
    });
    expect(onChange).toHaveBeenLastCalledWith({ unit: "days" });
  });

  it("shows seven file formats and structured table columns", () => {
    const fileNode = node("file_output");
    fileNode.data.format = "xlsx";
    const onChange = vi.fn();
    const onOpenVariableCenter = vi.fn();
    render(
      <WorkflowFileDataNodeConfig
        data={fileNode.data}
        declarations={declarations}
        edges={[]}
        node={fileNode}
        nodes={[fileNode]}
        onChange={onChange}
        onOpenVariableCenter={onOpenVariableCenter}
      />,
    );

    for (const name of ["纯文本（TXT）", "Markdown", "JSON", "CSV 表格", "PDF 文档", "Word 文档", "Excel 工作簿"]) {
      expect(screen.getByText(name, { selector: "span" })).toBeInTheDocument();
    }
    expect(screen.getByRole("textbox", { name: "表格列 1 字段" })).toHaveValue("id");
    fireEvent.change(screen.getByRole("textbox", { name: "表格列 1 标题" }), {
      target: { value: "编号" },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      columns: [{ id: "column_1", field: "id", label: "编号" }],
    });
    fireEvent.click(screen.getByRole("button", { name: "管理全局变量" }));
    expect(onOpenVariableCenter).toHaveBeenCalledOnce();
  });
});
