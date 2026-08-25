import { describe, expect, it } from "vitest";

import {
  isIterationV2,
  migrateLegacyIteration,
} from "./workflowIterationMigration";

describe("workflow iteration migration", () => {
  const legacy = {
    kind: "iteration" as const,
    title: "迭代处理",
    description: "legacy",
    inputVariable: "items",
    iterationVariable: "row",
    itemTemplate: "{{row}}/{{prefix}}",
    outputVariable: "mapped",
  };

  it("migrates a known JSON-array source without changing stable node data", () => {
    const result = migrateLegacyIteration(
      legacy,
      new Set(["items", "prefix"]),
      "json",
    );

    expect(result.ok).toBe(true);
    expect(result.data).toMatchObject({
      contractVersion: 2,
      mode: "template_map",
      inputVariable: "items",
      itemVariable: "row",
      indexVariable: "item_index",
      outputVariable: "mapped",
    });
    expect(result.data).not.toHaveProperty("iterationVariable");
    expect(isIterationV2(result.data!)).toBe(true);
  });

  it("blocks text/comma sources and missing template variables", () => {
    expect(
      migrateLegacyIteration(legacy, new Set(["items", "prefix"]), "text"),
    ).toMatchObject({ ok: false, message: expect.stringContaining("JSON 数组") });
    expect(
      migrateLegacyIteration(legacy, new Set(["items"]), "json"),
    ).toMatchObject({ ok: false, message: expect.stringContaining("prefix") });
  });

  it("chooses an isolated index name and blocks an output collision", () => {
    const isolated = migrateLegacyIteration(
      {
        ...legacy,
        iterationVariable: "item_index",
        itemTemplate: "{{item_index}}/{{prefix}}",
      },
      new Set(["items", "prefix", "batch_index"]),
      "json",
    );
    expect(isolated).toMatchObject({
      ok: true,
      data: { itemVariable: "item_index", indexVariable: "current_index" },
    });

    expect(
      migrateLegacyIteration(
        { ...legacy, outputVariable: "row" },
        new Set(["items", "prefix"]),
        "json",
      ),
    ).toMatchObject({ ok: false, message: expect.stringContaining("同名") });
  });
});
