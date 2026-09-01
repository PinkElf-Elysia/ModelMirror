import { describe, expect, it } from "vitest";
import {
  createDataTableNodeData,
  createTypedCanvasNodeData,
  normalizeRecentlyEnabledNodeData,
} from "./workflowDataTableNodeDefaults";

describe("Agent Table workflow node defaults", () => {
  it.each([
    ["data_table_query", "查询数据表", "table_records"],
    ["data_table_insert", "新增数据", "inserted_record"],
    ["data_table_update", "更新数据", "update_result"],
    ["data_table_delete", "删除数据", "delete_result"],
  ] as const)(
    "creates %s without falling back to the output node",
    (kind, title, outputVariable) => {
      const data = createDataTableNodeData(kind);

      expect(data).toMatchObject({ kind, title, outputVariable });
      expect(data?.title).not.toBe("最终交付");
    },
  );

  it("ignores unrelated node kinds", () => {
    expect(createDataTableNodeData("output")).toBeNull();
  });

  it("repairs database nodes saved with the old output fallback", () => {
    expect(
      normalizeRecentlyEnabledNodeData({
        kind: "data_table_query",
        title: "最终交付",
        description: "把指定变量作为工作流结果交付。",
        outputVariable: "llm_output",
      }),
    ).toMatchObject({
      kind: "data_table_query",
      title: "查询数据表",
      outputVariable: "table_records",
      versionPolicy: "latest",
    });
  });

  it("does not rewrite an already configured database node", () => {
    const configured = {
      ...createDataTableNodeData("data_table_query")!,
      tableId: "table-1",
      title: "客户查询",
    };

    expect(normalizeRecentlyEnabledNodeData(configured)).toEqual(configured);
  });

  it.each([
    ["json_serialize", "JSON 序列化", "json_text"],
    ["json_deserialize", "JSON 反序列化", "json_value"],
  ] as const)("creates %s defaults", (kind, title, outputVariable) => {
    expect(createTypedCanvasNodeData(kind)).toMatchObject({
      kind,
      title,
      contractVersion: 2,
      outputVariable,
    });
  });

  it("creates JSON deserialize with an explicit non-narrowing V2 schema", () => {
    expect(createTypedCanvasNodeData("json_deserialize")).toMatchObject({
      contractVersion: 2,
      expectedSchema: { type: "any" },
    });
  });

  it("creates an annotation without an output variable", () => {
    expect(createTypedCanvasNodeData("annotation")).toEqual({
      kind: "annotation",
      title: "注释",
      description: "仅保存画布说明，不参与控制流或运行。",
      content: "",
    });
  });

  it.each([
    ["json_serialize", "json_text"],
    ["json_deserialize", "json_value"],
    ["annotation", undefined],
  ] as const)("repairs a legacy %s output fallback", (kind, outputVariable) => {
    const normalized = normalizeRecentlyEnabledNodeData({
      kind,
      title: "最终交付",
      description: "把指定变量作为工作流结果交付。",
      outputVariable: "llm_output",
    });

    expect(normalized.kind).toBe(kind);
    expect(normalized.title).not.toBe("最终交付");
    expect(normalized.outputVariable).toBe(outputVariable);
  });
});
