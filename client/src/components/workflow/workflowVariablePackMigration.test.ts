import { describe, expect, it } from "vitest";

import type { WorkflowNode } from "../../types/workflow";
import { migrateLegacyVariableAggregator } from "./workflowVariablePackMigration";

function legacyNode(outputTemplate = ""): WorkflowNode {
  return {
    id: "legacy-pack",
    type: "workflowNode",
    position: { x: 40, y: 80 },
    data: {
      kind: "variable_aggregator",
      title: "旧变量聚合",
      description: "旧配置",
      variableNames: "customer, order",
      outputTemplate,
      outputVariable: "bundle",
    },
  };
}

describe("variable pack explicit migration", () => {
  it("moves a JSON-string aggregate to stable typed bindings when downstream accepts JSON", () => {
    const legacy = legacyNode();
    const output: WorkflowNode = {
      id: "output",
      type: "workflowNode",
      position: { x: 300, y: 80 },
      data: {
        kind: "output",
        title: "输出",
        description: "输出",
        outputVariable: "bundle",
      },
    };
    const result = migrateLegacyVariableAggregator(
      legacy,
      [legacy, output],
      [{ id: "edge-1", source: legacy.id, target: output.id }],
      new Set(["customer", "order"]),
    );

    expect(result).toMatchObject({
      ok: true,
      data: {
        kind: "variable_aggregator",
        contractVersion: 2,
        outputVariable: "bundle",
        bindings: [
          { id: "binding_1", sourceVariable: "customer", outputField: "customer" },
          { id: "binding_2", sourceVariable: "order", outputField: "order" },
        ],
      },
    });
    expect(legacy.data).not.toHaveProperty("bindings");
  });

  it("expands the legacy name/value template into variable assignment V2", () => {
    const legacy = legacyNode("## {name}\n{value}\n");
    const result = migrateLegacyVariableAggregator(
      legacy,
      [legacy],
      [],
      new Set(["customer", "order"]),
    );

    expect(result).toMatchObject({
      ok: true,
      data: {
        kind: "variable_assign",
        contractVersion: 2,
        valueSource: "template",
        template: (
          "## customer\n{{customer}}\n"
          + "## order\n{{order}}\n"
        ),
        outputVariable: "bundle",
      },
    });
  });

  it("blocks unavailable inputs, output overwrite, unsafe braces, and text-only descendants", () => {
    const legacy = legacyNode();
    expect(migrateLegacyVariableAggregator(
      legacy,
      [legacy],
      [],
      new Set(["customer"]),
    )).toMatchObject({ ok: false, message: expect.stringContaining("不可用") });

    const overlap = legacyNode();
    overlap.data.outputVariable = "customer";
    expect(migrateLegacyVariableAggregator(
      overlap,
      [overlap],
      [],
      new Set(["customer", "order"]),
    )).toMatchObject({ ok: false, message: expect.stringContaining("不能覆盖") });

    expect(migrateLegacyVariableAggregator(
      legacyNode("{{{value}}}"),
      [legacy],
      [],
      new Set(["customer", "order"]),
    )).toMatchObject({ ok: false, message: expect.stringContaining("花括号") });

    const textConsumer: WorkflowNode = {
      id: "text-consumer",
      type: "workflowNode",
      position: { x: 300, y: 80 },
      data: {
        kind: "llm",
        title: "文本消费者",
        description: "文本",
        modelId: "test",
        prompt: "{{bundle}}",
        outputVariable: "answer",
      },
    };
    expect(migrateLegacyVariableAggregator(
      legacy,
      [legacy, textConsumer],
      [{ id: "edge-1", source: legacy.id, target: textConsumer.id }],
      new Set(["customer", "order"]),
    )).toMatchObject({ ok: false, message: expect.stringContaining("没有明确接受 JSON") });
  });
});
