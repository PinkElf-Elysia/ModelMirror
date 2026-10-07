import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { WorkflowNode, WorkflowNodeData } from "../../types/workflow";
import WorkflowControlledWriteConfig, { type WorkflowWriteGrant } from "./WorkflowControlledWriteConfig";

const grant: WorkflowWriteGrant = { table_id: "table-demo", operations: ["insert", "update", "delete"], writable_fields: ["status"], max_affected_rows: 1 };
const sources: WorkflowNode[] = [
  { id: "q", type: "workflowNode", position: { x: 0, y: 0 }, data: { kind: "data_table_query", title: "真实查询", description: "", tableId: "table-demo", plannerRef: "lookup" } },
  { id: "fake", type: "workflowNode", position: { x: 0, y: 100 }, data: { kind: "workflow_agent", title: "伪造记录", description: "", plannerRef: "agent" } },
  { id: "foreign", type: "workflowNode", position: { x: 0, y: 200 }, data: { kind: "data_table_query", title: "其他表", description: "", tableId: "table-other", plannerRef: "other" } },
  { id: "json", type: "workflowNode", position: { x: 0, y: 300 }, data: { kind: "json_deserialize", title: "字段校验", description: "", contractVersion: 2, plannerRef: "checked" } },
];

function Harness() {
  const [data, setData] = useState<WorkflowNodeData>({ kind: "data_table_update", title: "更新", description: "", tableId: "table-demo", pinnedSchemaVersion: 1, plannerRef: "write", plannerAdapterConfigV1: { value_source: "literal", values: { status: "open" }, filter: { ref: "selected", field: "status", operator: "eq", value: "open" }, max_affected_rows: 1 } });
  return <MemoryRouter><WorkflowControlledWriteConfig data={data} nodes={sources} grants={[grant]} onChange={(patch) => setData((before) => ({ ...before, ...patch }))} /><output aria-label="编辑请求">{JSON.stringify(data.plannerWriteIntentV2 ?? {})}</output></MemoryRouter>;
}

const current = () => JSON.parse(screen.getByLabelText("编辑请求").textContent ?? "{}");

describe("受控写入语义编辑", () => {
  it("记录只能选同表 Query/Insert，编辑不构造原生变量、授权或版本", () => {
    render(<Harness />);
    const select = screen.getByLabelText("可信记录来源");
    expect(select).toHaveTextContent("真实查询");
    expect(select).not.toHaveTextContent("伪造记录");
    expect(select).not.toHaveTextContent("其他表");
    fireEvent.change(select, { target: { value: "lookup" } });
    expect(current().inputs).toEqual([{ port: "records", source_ref: "lookup", source_port: "result" }]);
    for (const field of ["tableId", "writeGrant", "recordsVariable", "pinnedSchemaVersion", "sourceHandle"]) expect(JSON.stringify(current())).not.toContain(field);
    expect(screen.getByLabelText("最多影响行数")).toHaveAttribute("max", "1");
  });

  it("无效 JSON 保留为无效请求，不能悄悄提交上一次的合法值", () => {
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("固定业务对象"), { target: { value: "{bad" } });
    expect(screen.getByLabelText("固定业务对象")).toHaveAttribute("aria-invalid", "true");
    expect(current().config.values).toBe("{bad");
    fireEvent.change(screen.getByLabelText("固定业务对象"), { target: { value: '{"status":"closed"}' } });
    expect(current().config.values).toEqual({ status: "closed" });
    expect(screen.getByLabelText("固定业务对象")).toHaveAttribute("aria-invalid", "false");
  });

  it("输入模式清除固定值并只绑定校验来源", () => {
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("业务值来源"), { target: { value: "input" } });
    fireEvent.change(screen.getByLabelText("业务值校验来源"), { target: { value: "checked" } });
    expect(current().config.values).toBeNull();
    expect(current().inputs).toEqual([{ port: "values", source_ref: "checked", source_port: "value" }]);
  });
});
