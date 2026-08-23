import { describe, expect, it } from "vitest";

import type { WorkflowEdge, WorkflowNodeData } from "../../types/workflow";
import {
  migrateLegacyParameterExtractor,
  migrateLegacyQuestionClassifier,
  nextStableId,
} from "./workflowTypedAiMigration";

const legacyExtractor = (): WorkflowNodeData => ({
  kind: "parameter_extractor",
  title: "参数提取器",
  description: "旧版",
  inputVariable: "user_input",
  outputVariable: "parameters_json",
  modelId: "model-a",
  schema: "order_id: 订单号\namount: 金额",
});

const legacyClassifier = (): WorkflowNodeData => ({
  kind: "question_classifier",
  title: "问题分类",
  description: "旧版",
  inputVariable: "user_input",
  outputVariable: "category",
  categories: '{"投诉":["投诉","退款"],"咨询":["怎么","如何"]}',
  defaultCategory: "其他",
  matchMode: "contains_any",
  caseSensitive: "false",
  useLlmFallback: "false",
});

describe("typed AI explicit migrations", () => {
  it("losslessly parses legacy extractor lines into stable V2 fields", () => {
    const result = migrateLegacyParameterExtractor(legacyExtractor());
    expect(result.ok).toBe(true);
    expect(result.patch).toMatchObject({
      contractVersion: 2,
      schemaMode: "fields",
      outputShape: "object",
      repairAttempts: 0,
      fields: [
        { id: "field_1", name: "order_id", description: "订单号" },
        { id: "field_2", name: "amount", description: "金额" },
      ],
    });
  });

  it("refuses ambiguous extractor descriptions instead of guessing", () => {
    const data = legacyExtractor();
    data.schema = "没有分隔符";
    expect(migrateLegacyParameterExtractor(data)).toMatchObject({ ok: false });
  });

  it("copies one ordinary legacy classifier edge to every stable outlet", () => {
    const edge = {
      id: "edge-old",
      source: "classifier",
      target: "output",
    } as WorkflowEdge;
    const result = migrateLegacyQuestionClassifier(legacyClassifier(), [edge]);
    expect(result.ok).toBe(true);
    expect(result.patch?.categoriesV2).toEqual([
      expect.objectContaining({ id: "category_1", label: "投诉" }),
      expect.objectContaining({ id: "category_2", label: "咨询" }),
    ]);
    expect(result.outgoingEdges?.map((item) => item.sourceHandle)).toEqual([
      "category_1",
      "category_2",
      "default",
    ]);
    expect(new Set(result.outgoingEdges?.map((item) => item.id)).size).toBe(3);
  });

  it("allocates migrated edge IDs without colliding with unrelated edges", () => {
    const edge = {
      id: "edge-old",
      source: "classifier",
      target: "output",
    } as WorkflowEdge;
    const result = migrateLegacyQuestionClassifier(
      legacyClassifier(),
      [edge],
      ["edge-old", "edge-old-category_1", "edge-old-default"],
    );
    expect(result.outgoingEdges?.map((item) => item.id)).toEqual([
      "edge-old-category_1-2",
      "edge-old-category_2",
      "edge-old-default-2",
    ]);
  });

  it("refuses category labels that collide after trimming", () => {
    const data = legacyClassifier();
    data.categories = '{"退款":["退款"]," 退款 ":["退钱"]}';
    expect(migrateLegacyQuestionClassifier(data, [])).toMatchObject({ ok: false });
  });

  it("refuses classifier migration with multiple or custom outgoing edges", () => {
    const custom = {
      id: "edge-custom",
      source: "classifier",
      sourceHandle: "legacy-special",
      target: "output",
    } as WorkflowEdge;
    expect(migrateLegacyQuestionClassifier(legacyClassifier(), [custom])).toMatchObject({ ok: false });
    expect(migrateLegacyQuestionClassifier(legacyClassifier(), [
      { ...custom, id: "one", sourceHandle: undefined },
      { ...custom, id: "two", sourceHandle: undefined },
    ])).toMatchObject({ ok: false });
  });

  it("reuses holes without renumbering existing stable IDs", () => {
    expect(nextStableId("category", ["category_1", "category_3"], 8)).toBe("category_2");
    expect(nextStableId("rule", ["rule_1", "rule_2"], 2)).toBeNull();
  });
});
