import { describe, expect, it } from "vitest";

import type { WorkflowNodeData } from "../../types/workflow";
import {
  migrateLegacyCodeNode,
  migrateLegacyTemplateTransform,
} from "./workflowSafeTextMigration";

describe("safe text explicit migrations", () => {
  it("moves a safe Code V1 operation to the V2 contract without Python fields", () => {
    const legacy: WorkflowNodeData = {
      kind: "code",
      title: "旧代码",
      description: "旧版",
      codeOperation: "replace",
      codeInputVariable: "source_value",
      codeOutputVariable: "clean_value",
      replaceFrom: "old",
      replaceTo: "new",
      pythonCode: "print(input)",
    };

    const result = migrateLegacyCodeNode(legacy, new Set(["source_value"]));

    expect(result.ok).toBe(true);
    expect(result.data).toEqual({
      kind: "code",
      title: "安全文本加工",
      description: "把变量稳定转换为文本后，执行受控的大小写、替换或拼接操作。",
      contractVersion: 2,
      inputVariable: "source_value",
      outputVariable: "clean_value",
      operation: "replace",
      replaceFrom: "old",
      replaceTo: "new",
      concatValue: "",
    });
    expect(result.data).not.toHaveProperty("pythonCode");
    expect(legacy).toHaveProperty("pythonCode", "print(input)");
  });

  it("refuses Python and invalid variable names instead of guessing", () => {
    expect(migrateLegacyCodeNode({
      kind: "code",
      title: "Python",
      description: "旧版",
      codeOperation: "python",
      pythonCode: "print(input)",
    }, new Set(["llm_output"]))).toMatchObject({ ok: false, message: expect.stringContaining("无法无损迁移") });

    expect(migrateLegacyCodeNode({
      kind: "code",
      title: "旧版",
      description: "旧版",
      codeOperation: "upper",
      codeInputVariable: "bad-name",
      codeOutputVariable: "result",
    }, new Set(["bad-name"]))).toMatchObject({ ok: false, message: expect.stringContaining("输入变量名不合法") });

    expect(migrateLegacyCodeNode({
      kind: "code",
      title: "旧版",
      description: "旧版",
      codeOperation: "upper",
      codeInputVariable: "later_value",
      codeOutputVariable: "result",
    }, new Set(["user_input"]))).toMatchObject({ ok: false, message: expect.stringContaining("不能保证") });

    expect(migrateLegacyCodeNode({
      kind: "code",
      title: "旧版",
      description: "旧版",
      codeOperation: "concat",
      codeInputVariable: "user_input",
      codeOutputVariable: "result",
      concatValue: "x".repeat(100_001),
    }, new Set(["user_input"]))).toMatchObject({ ok: false, message: expect.stringContaining("100000") });
  });

  it("does not reinterpret malformed contract versions as V1 or V2", () => {
    for (const contractVersion of ["2", "02", "+2", 2.9]) {
      expect(migrateLegacyCodeNode({
        kind: "code",
        title: "版本异常",
        description: "异常草稿",
        contractVersion,
        codeOperation: "upper",
        codeInputVariable: "user_input",
        codeOutputVariable: "result",
      }, new Set(["user_input"]))).toMatchObject({
        ok: false,
        message: expect.stringContaining("合同版本"),
      });
    }
  });

  it("migrates a retired template node to Variable Assign V2", () => {
    const result = migrateLegacyTemplateTransform({
      kind: "template_transform",
      title: "模板转换",
      description: "旧版",
      template: "订单：{{order_id}}",
      outputVariable: "report_text",
    }, new Set(["order_id"]));

    expect(result).toMatchObject({
      ok: true,
      data: {
        kind: "variable_assign",
        contractVersion: 2,
        valueSource: "template",
        template: "订单：{{order_id}}",
        outputVariable: "report_text",
      },
    });
  });

  it("blocks a template migration that would create invalid V2 data", () => {
    expect(migrateLegacyTemplateTransform({
      kind: "template_transform",
      title: "模板转换",
      description: "旧版",
      template: "   ",
      outputVariable: "bad-name",
    }, new Set())).toMatchObject({ ok: false, message: expect.stringContaining("模板内容不能为空") });

    expect(migrateLegacyTemplateTransform({
      kind: "template_transform",
      title: "模板转换",
      description: "旧版",
      template: "{{order.id}}",
      outputVariable: "result",
    }, new Set(["order"]))).toMatchObject({ ok: false, message: expect.stringContaining("花括号") });

    expect(migrateLegacyTemplateTransform({
      kind: "template_transform",
      title: "模板转换",
      description: "旧版",
      template: "{{{order_id}}}",
      outputVariable: "result",
    }, new Set(["order_id"]))).toMatchObject({ ok: false, message: expect.stringContaining("花括号") });

    expect(migrateLegacyTemplateTransform({
      kind: "template_transform",
      title: "模板转换",
      description: "旧版",
      template: "{{later_value}}",
      outputVariable: "result",
    }, new Set(["user_input"]))).toMatchObject({ ok: false, message: expect.stringContaining("不可用") });

    expect(migrateLegacyTemplateTransform({
      kind: "template_transform",
      title: "模板转换",
      description: "旧版",
      template: "x".repeat(100_001),
      outputVariable: "result",
    }, new Set())).toMatchObject({ ok: false, message: expect.stringContaining("100000") });
  });
});
