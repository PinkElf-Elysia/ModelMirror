import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import EvaluationWriteCases, {
  normalizeEvaluationWriteCaseForSave,
  parseExpectedWriteSubsetJson,
  parseInitializationRecordsJson,
  type EvaluationWriteCase,
} from "./EvaluationWriteCases";

function Harness() {
  const [cases, setCases] = useState<EvaluationWriteCase[]>([
    { case_id: "case_1", name: "写入订单", message: "更新订单状态" },
  ]);
  const [editorState, setEditorState] = useState({
    has_unapplied_changes: false,
    has_invalid_changes: false,
  });
  return (
    <>
      <EvaluationWriteCases
        cases={cases}
        onChange={setCases}
        onEditorStateChange={setEditorState}
      />
      <output aria-label="当前写入用例">{JSON.stringify(cases)}</output>
      <output aria-label="写入 JSON 编辑状态">{JSON.stringify(editorState)}</output>
    </>
  );
}

function currentCase(): EvaluationWriteCase {
  return JSON.parse(screen.getByLabelText("当前写入用例").textContent ?? "[]")[0];
}

function currentEditorState() {
  return JSON.parse(screen.getByLabelText("写入 JSON 编辑状态").textContent ?? "{}");
}

describe("EvaluationWriteCases", () => {
  it("只接受本地 JSON 初始化记录并拒绝系统字段、重复 ref 与 201 行", () => {
    expect(parseInitializationRecordsJson(JSON.stringify([
      { ref: "order_a", data: { status: "pending", _fixture: true } },
    ]))).toEqual([
      { ref: "order_a", data: { status: "pending", _fixture: true } },
    ]);

    expect(() => parseInitializationRecordsJson(JSON.stringify([
      { ref: "order_a", data: { record_id: "forged" } },
    ]))).toThrow("无效业务字段");
    expect(() => parseInitializationRecordsJson(JSON.stringify([
      { ref: "order_a", data: {} },
      { ref: "order_a", data: {} },
    ]))).toThrow("ref 不能重复");
    expect(() => parseInitializationRecordsJson(JSON.stringify(
      Array.from({ length: 201 }, (_, index) => ({ ref: `row_${index}`, data: {} })),
    ))).toThrow("最多 200 行");
  });

  it("严格校验效果字段子集和每例边界", () => {
    expect(parseExpectedWriteSubsetJson('{"status":"done"}'))
      .toEqual({ status: "done" });
    expect(() => parseExpectedWriteSubsetJson('{"updated_at":1}'))
      .toThrow("无效业务字段");
    expect(() => normalizeEvaluationWriteCaseForSave({
      message: "超限",
      table_initializations: Array.from({ length: 21 }, (_, index) => ({
        table_id: `table-${index}`,
        schema_version: 1,
        source: "synthetic" as const,
        records: [],
      })),
    })).toThrow("最多初始化 20 张表");
    expect(() => normalizeEvaluationWriteCaseForSave({
      message: "错误 noop",
      effects: [{
        node_ref: "write_order",
        table_id: "table-orders",
        operation: "update",
        status: "noop",
        affected_count: 1,
        expected_before: {},
        expected_after: {},
      }],
    })).toThrow("affected_count 设为 0");
  });

  it("保留合法的合成初始化和类型化写效果", () => {
    const normalized = normalizeEvaluationWriteCaseForSave({
      case_id: "case_1",
      message: "更新订单",
      table_initializations: [{
        table_id: "table-orders",
        schema_version: 4,
        source: "synthetic",
        records: [{ ref: "order_a", data: { status: "pending" } }],
      }],
      effects: [{
        node_ref: "write_order",
        table_id: "table-orders",
        operation: "update",
        schema_version: null,
        contract_checksum: null,
        status: "applied",
        affected_count: 1,
        expected_before: { status: "pending" },
        expected_after: { status: "done" },
        error_code: null,
      }],
    });

    expect(normalized.table_initializations?.[0]).toMatchObject({
      table_id: "table-orders",
      source: "synthetic",
      records: [{ ref: "order_a", data: { status: "pending" } }],
    });
    expect(normalized.effects?.[0]).toMatchObject({
      node_ref: "write_order",
      operation: "update",
      affected_count: 1,
      expected_after: { status: "done" },
    });
    expect(normalized.effects?.[0]).not.toHaveProperty("schema_version");
    expect(normalized.effects?.[0]).not.toHaveProperty("contract_checksum");
    expect(normalized.effects?.[0]).not.toHaveProperty("error_code");
  });

  it("逐例添加初始化和效果时保持显式默认值与状态联动", () => {
    render(<Harness />);

    expect(screen.queryByText("导入活表")).toBeNull();
    fireEvent.click(screen.getByText("写入订单"));
    fireEvent.click(screen.getByRole("button", { name: "添加初始化表" }));
    fireEvent.change(screen.getByLabelText("数据表 ID"), {
      target: { value: "table-orders" },
    });
    fireEvent.change(screen.getByLabelText("输入来源"), {
      target: { value: "synthetic" },
    });
    fireEvent.change(screen.getByRole("textbox", {
      name: "table-orders 初始化记录 JSON",
    }), {
      target: {
        value: '[{"ref":"order_a","data":{"status":"pending"}}]',
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "应用记录" }));

    fireEvent.click(screen.getByRole("button", { name: "添加效果断言" }));
    expect(currentCase().effects).toEqual([{
      node_ref: "",
      table_id: "",
      operation: "insert",
      status: "applied",
      affected_count: 1,
      expected_before: {},
      expected_after: {},
    }]);
    fireEvent.change(screen.getByLabelText("预期状态"), {
      target: { value: "noop" },
    });

    expect(currentCase().table_initializations).toEqual([{
      table_id: "table-orders",
      schema_version: 1,
      source: "synthetic",
      records: [{ ref: "order_a", data: { status: "pending" } }],
    }]);
    expect(currentCase().effects?.[0]).toMatchObject({
      status: "noop",
      affected_count: 0,
    });
    expect((screen.getByLabelText("影响行数") as HTMLInputElement).disabled).toBe(true);
  });

  it("向父页上报未应用和无效 JSON，修正并应用后解除阻断", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("写入订单"));
    fireEvent.click(screen.getByRole("button", { name: "添加初始化表" }));
    fireEvent.change(screen.getByRole("textbox", {
      name: "初始化表 1 初始化记录 JSON",
    }), { target: { value: "not-json" } });
    fireEvent.click(screen.getByRole("button", { name: "应用记录" }));

    expect(screen.getByText("初始化记录必须是合法 JSON 数组。").textContent)
      .toContain("合法 JSON 数组");
    expect(currentCase().table_initializations?.[0].records).toEqual([]);
    expect(currentEditorState()).toEqual({
      has_unapplied_changes: true,
      has_invalid_changes: true,
    });

    fireEvent.change(screen.getByRole("textbox", {
      name: "初始化表 1 初始化记录 JSON",
    }), {
      target: { value: '[{"ref":"order_a","data":{"status":"pending"}}]' },
    });
    expect(currentEditorState()).toEqual({
      has_unapplied_changes: true,
      has_invalid_changes: false,
    });

    fireEvent.click(screen.getByRole("button", { name: "应用记录" }));
    expect(currentEditorState()).toEqual({
      has_unapplied_changes: false,
      has_invalid_changes: false,
    });
    expect(currentCase().table_initializations?.[0].records).toEqual([
      { ref: "order_a", data: { status: "pending" } },
    ]);
  });
});
